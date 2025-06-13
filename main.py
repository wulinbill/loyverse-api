import os
import time
import logging
import traceback
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

# -------------------- 配置 --------------------
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LOYVERSE_REDIRECT_URI")  # e.g. "https://your-domain.com/callback"
API_BASE = "https://api.loyverse.com/v1.0"
STORE_ID = os.getenv("LOYVERSE_STORE_ID")

# 内存缓存 token；重启后会丢失，生产可改成写文件/数据库
_access_token = None
_refresh_token = None
_expire_time = 0

# -------------------- 应用初始化 --------------------
app = Flask(__name__)
CORS(app)  # 允许所有来源调用

# -------------------- OAuth 回调 --------------------
@app.route("/callback", methods=["GET"])
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400

    resp = requests.post(f"{API_BASE}/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=10)
    if not resp.ok:
        return jsonify({"error": "授权失败", "details": resp.text}), resp.status_code

    data = resp.json()
    global _access_token, _refresh_token, _expire_time
    _access_token = data["access_token"]
    _refresh_token = data["refresh_token"]
    _expire_time = time.time() + data.get("expires_in", 3600)
    return jsonify({"message": "授权成功"}), 200

def _refresh_token_if_needed():
    global _access_token, _refresh_token, _expire_time
    # 如果没 token 或快过期，刷新
    if not _refresh_token:
        raise RuntimeError("尚未授权，请先访问 /callback")
    if time.time() > _expire_time - 60:
        resp = requests.post(f"{API_BASE}/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": _refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _access_token = data["access_token"]
        _refresh_token = data["refresh_token"]
        _expire_time = time.time() + data.get("expires_in", 3600)

def loyverse_headers():
    _refresh_token_if_needed()
    return {"Authorization": f"Bearer {_access_token}"}

# -------------------- 根路由 --------------------
@app.route("/", methods=["GET"])
def index():
    return "Loyverse OAuth App is running.", 200

# -------------------- 获取菜单 --------------------
@app.route("/get_menu", methods=["POST"])
def get_menu():
    try:
        items = []
        cursor = None
        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(f"{API_BASE}/items",
                                headers=loyverse_headers(),
                                params=params,
                                timeout=15)
            resp.raise_for_status()
            data = resp.json()

            for it in data.get("items", []):
                variants = []
                for v in it.get("variants", []):
                    for s in v.get("stores", []):
                        if str(s["store_id"]) == str(STORE_ID) and s.get("available_for_sale"):
                            variants.append({
                                "variant_id": v["variant_id"],
                                "price": s["price"]
                            })
                if not variants:
                    continue
                items.append({
                    "sku": variants[0]["variant_id"],
                    "name": it["item_name"],
                    "category": it.get("category_id"),
                    "price_base": variants[0]["price"],
                    "aliases": []
                })

            cursor = data.get("cursor")
            if not cursor:
                break

        return jsonify({"menu": items})
    except Exception as e:
        logging.error("get_menu error: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -------------------- 查询客户 --------------------
@app.route("/get_customer", methods=["POST"])
def get_customer():
    try:
        body = request.get_json(force=True)
        phone = body.get("phone")
        resp = requests.get(f"{API_BASE}/customers",
                            headers=loyverse_headers(),
                            params={"phone_number": phone, "limit": 50},
                            timeout=15)
        resp.raise_for_status()
        custs = resp.json().get("customers", [])
        if custs:
            c = custs[0]
            return jsonify({"customer_id": c["id"], "name": c["name"]})
        return jsonify({"customer_id": None, "name": None})
    except Exception as e:
        logging.error("get_customer error: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -------------------- 创建客户 --------------------
@app.route("/create_customer", methods=["POST"])
def create_customer():
    try:
        data = request.get_json(force=True)
        if "name" not in data or "phone" not in data:
            return jsonify({"error": "name & phone are required"}), 400

        payload = {"name": data["name"], "phone_number": data["phone"]}
        resp = requests.post(f"{API_BASE}/customers",
                             headers=loyverse_headers(),
                             json=payload,
                             timeout=15)
        resp.raise_for_status()
        return jsonify({"customer_id": resp.json()["id"]})
    except Exception as e:
        logging.error("create_customer error: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -------------------- 下单 --------------------
@app.route("/place_order", methods=["POST"])
def place_order():
    try:
        data = request.get_json(force=True)
        items = data.get("items", [])
        if not items:
            return jsonify({"error": "items array is required"}), 400

        body = {
            "customer_id": data.get("customer_id"),
            "store_id": STORE_ID,
            "dining_option": "TAKEAWAY",
            "line_items": [
                {"variant_id": it["variant_id"], "quantity": it["quantity"]}
                for it in items
            ],
            "payments": [
                {
                    # 只传 Loyverse 后台「现金」支付方式的 ID 和金额
                    "payment_type_id": os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID"),
                    "money_amount": data.get("total_money", 0)
                }
            ]
        }
        resp = requests.post(f"{API_BASE}/receipts",
                             headers=loyverse_headers(),
                             json=body,
                             timeout=15)
        resp.raise_for_status()
        r = resp.json()
        return jsonify({
            "receipt_number": r.get("receipt_number"),
            "total_money": r.get("total_money")
        })
    except Exception as e:
        logging.error("place_order error: %s", e)
        traceback.print_exc()
        # 如果是 HTTPError 且返回了 JSON，则直接透传
        if isinstance(e, requests.HTTPError) and e.response is not None:
            try:
                return jsonify(e.response.json()), e.response.status_code
            except:
                pass
        return jsonify({"error": str(e)}), 500

# -------------------- 全局异常捕获 --------------------
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    return jsonify({"error": str(err)}), 500

# -------------------- 启动 --------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
