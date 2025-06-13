import os
import logging
import traceback
from datetime import datetime
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import requests

# -------------------- 配置 --------------------
CLIENT_ID     = os.getenv("LOY_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOY_CLIENT_SECRET")
API_BASE      = "https://api.loyverse.com/v1.0"
REDIRECT_URI  = os.getenv("LOY_REDIRECT_URI")
STORE_ID      = os.getenv("LOY_STORE_ID")
# Loyverse 后台定义好的现金支付类型 ID
CASH_PAYMENT_TYPE_ID = os.getenv("LOY_CASH_PAYMENT_TYPE_ID")

app = Flask(__name__)
CORS(app)  # 开启全局 CORS

# 存放当前的 access_token / refresh_token
_tokens = {"access_token": None, "refresh_token": None}

# -------------------- OAuth 相关 --------------------
@app.route("/callback")
def oauth_callback():
    code = request.args.get("code")
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        json={
            "grant_type":    "authorization_code",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "code":          code,
        },
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    _tokens["access_token"]  = data["access_token"]
    _tokens["refresh_token"] = data["refresh_token"]
    return "授权成功，你可以关掉此页。"

def _refresh_token():
    """内部方法：用 refresh_token 换新的 access_token."""
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        json={
            "grant_type":    "refresh_token",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": _tokens["refresh_token"],
        },
        timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    _tokens["access_token"]  = data["access_token"]
    _tokens["refresh_token"] = data["refresh_token"]

def loyverse_headers():
    if not _tokens["access_token"]:
        raise RuntimeError("尚未授权，请先访问 /callback")
    return {"Authorization": f"Bearer {_tokens['access_token']}"}

# -------------------- 工具接口 --------------------
@app.route("/")
def home():
    return "Loyverse OAuth App is running."

@app.route("/get_menu", methods=["POST", "OPTIONS"])
def get_menu():
    """拉取全部可售商品菜单（含 SKU、名称、分类、基础价格）"""
    items = []
    cursor = None
    while True:
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{API_BASE}/items", headers=loyverse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            variants = []
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if str(s["store_id"]) == str(STORE_ID) and s.get("available_for_sale"):
                        variants.append({
                            "variant_id": v["variant_id"],
                            "price_base": s["price"]
                        })
            if not variants:
                continue
            first = variants[0]
            items.append({
                "variant_id": first["variant_id"],
                "item_name":  it["item_name"],
                "category_id": it.get("category_id"),
                "price_base": first["price_base"],
            })
        cursor = data.get("cursor")
        if not cursor:
            break
    return jsonify({"menu": items})

@app.route("/get_customer", methods=["POST", "OPTIONS"])
def get_customer():
    """根据来电号码查客户；VAPI Webhook 体里含 call.customer.number 或 customer.number"""
    body = request.json or {}
    # 优先从 webhook 的 call.customer.number 拿
    phone = (body.get("call", {})
                 .get("customer", {})
                 .get("number")
             ) or body.get("customer", {}).get("number") \
             or body.get("phone") \
             or ""
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 1},
        timeout=15
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        c = custs[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})

@app.route("/create_customer", methods=["POST", "OPTIONS"])
def create_customer():
    """新建客户（名称+电话）"""
    data = request.json or {}
    name  = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        return jsonify({"error": "name & phone are required"}), 400

    payload = {"name": name, "phone_number": phone}
    resp = requests.post(f"{API_BASE}/customers", headers=loyverse_headers(),
                         json=payload, timeout=15)
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json()["id"]})

@app.route("/place_order", methods=["POST", "OPTIONS"])
def place_order():
    """
    下单接口：
    1) 根据传入的 items 列表（variant_id, quantity），先拉一遍 MENU，构造 line_items 包含 price/cost；
    2) 本地计算总金额 total_money；
    3) 拼 payments[]，填充 money_amount、name、type、paid_at 等必填项，提交到 /receipts。
    """
    data = request.json or {}
    orders = data.get("items", [])
    if not orders:
        return jsonify({"error": "items array is required"}), 400

    # 1) 取菜单构造价格映射
    menu_resp = get_menu().get_json()
    price_map = { it["variant_id"]: it["price_base"] for it in menu_resp["menu"] }

    line_items = []
    total_money = 0
    for o in orders:
        vid = o.get("variant_id")
        qty = int(o.get("quantity", 0))
        price = price_map.get(vid)
        if price is None:
            return jsonify({"error": f"unknown variant_id {vid}"}), 400
        line_items.append({
            "variant_id": vid,
            "quantity": qty,
            "price": price,
            "cost": 0
        })
        total_money += price * qty

    # 2) 构造支付方式（现金）
    payments = [{
        "payment_type_id": CASH_PAYMENT_TYPE_ID,
        "money_amount": total_money,
        "name":         "Cash",
        "type":         "CASH",
        "paid_at":      datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }]

    # 3) 发到 Loyverse 创建收据
    body = {
        "customer_id": data.get("customer_id"),
        "store_id":    STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items":    line_items,
        "payments":      payments,
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(),
                         json=body, timeout=15)
    resp.raise_for_status()
    j = resp.json()
    return jsonify({
        "receipt_number": j.get("receipt_number"),
        "total_money":    j.get("total_money")
    })

# 全局异常捕获
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    resp = getattr(err, "response", None)
    payload = {"error": str(err), "type": err.__class__.__name__}
    if resp is not None:
        try:
            payload["detail"] = resp.json()
        except Exception:
            payload["detail_text"] = resp.text[:500]
        payload["status_code"] = resp.status_code
        return jsonify(payload), resp.status_code
    return jsonify(payload), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
