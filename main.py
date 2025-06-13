import os
import time
import json
import logging
import traceback
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import requests

# ------------------ 配置 ------------------
LOYVERSE_CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
LOYVERSE_CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
STORE_ID = os.getenv("LOYVERSE_STORE_ID")
API_BASE = "https://api.loyverse.com/v1.0"
# 本地文件存储 token，可根据需要改成 Redis 或数据库
TOKEN_FILE = "/tmp/loyverse_token.json"

# ------------------ 应用初始化 ------------------
app = Flask(__name__)
CORS(app)  # 允许跨域

# ------------------ Token 存取 ------------------
def load_token():
    try:
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_token(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)

def get_access_token():
    tok = load_token()
    # 如果没有 token 或者过期，就用 refresh_token 刷新
    if "access_token" not in tok or tok.get("expires_at", 0) < time.time():
        if "refresh_token" not in tok:
            raise RuntimeError("尚未授权，请先访问 /callback 完成授权")
        resp = requests.post(
            f"{API_BASE}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": LOYVERSE_CLIENT_ID,
                "client_secret": LOYVERSE_CLIENT_SECRET,
                "refresh_token": tok["refresh_token"],
            },
        )
        resp.raise_for_status()
        new_tok = resp.json()
        new_tok["expires_at"] = time.time() + new_tok.get("expires_in", 0)
        save_token(new_tok)
        tok = new_tok
    return tok["access_token"]

def loyverse_headers():
    return {"Authorization": f"Bearer {get_access_token()}"}

# ------------------ OAuth 回调 ------------------
@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "缺少 code 参数"}), 400
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": LOYVERSE_CLIENT_ID,
            "client_secret": LOYVERSE_CLIENT_SECRET,
            "code": code,
        },
    )
    resp.raise_for_status()
    tok = resp.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 0)
    save_token(tok)
    return jsonify({"status": "授权成功，请关闭此页面"})

# ------------------ 根路径 (仅 GET) ------------------
@app.route("/", methods=["GET"])
def home():
    return "Loyverse OAuth App is running!", 200

# ------------------ 获取菜单 ------------------
@app.route("/get_menu", methods=["POST"])
def get_menu():
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
                        variants.append({"variant_id": v["variant_id"], "price": s["price"]})
            if not variants:
                continue
            items.append({
                "sku": variants[0]["variant_id"],
                "name": it["item_name"],
                "category": it.get("category_id"),
                "price_base": variants[0]["price"],
                "aliases": [],
            })
        cursor = data.get("cursor")
        if not cursor:
            break
    return jsonify({"menu": items})

# ------------------ 查询客户 ------------------
@app.route("/get_customer", methods=["POST"])
def get_customer():
    if not request.is_json:
        return jsonify({"error": "Content-Type 必须是 application/json"}), 415
    phone = request.json.get("phone")
    if not phone:
        return jsonify({"error": "phone 参数必填"}), 400
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

# ------------------ 创建客户 ------------------
@app.route("/create_customer", methods=["POST"])
def create_customer():
    if not request.is_json:
        return jsonify({"error": "Content-Type 必须是 application/json"}), 415
    data = request.json
    if not data.get("name") or not data.get("phone"):
        return jsonify({"error": "name 和 phone 参数必填"}), 400
    resp = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json={"name": data["name"], "phone_number": data["phone"]},
        timeout=15
    )
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json()["id"]})

# ------------------ 下单 ------------------
@app.route("/place_order", methods=["POST"])
def place_order():
    if not request.is_json:
        return jsonify({"error": "Content-Type 必须是 application/json"}), 415
    data = request.json
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items 数组必填"}), 400
    # 构造请求体
    body = {
        "customer_id": data.get("customer_id"),
        "store_id": STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items": [{"variant_id": it["variant_id"], "quantity": it["quantity"]} for it in items],
        # 默认现金支付，只传 payment_type_id 和 money_amount
        "payments": [
            {
                "payment_type_id": data.get("payment_type_id"),  # 例如环境变量中注入现金支付类型
                "money_amount": data.get("money_amount")         # 传入客户端计算好的总额
            }
        ]
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=body, timeout=15)
    resp.raise_for_status()
    r = resp.json()
    return jsonify({"receipt_number": r.get("receipt_number"), "total_with_tax": r.get("total_money")})

# ------------------ 全局错误处理 ------------------
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    # 如果是 HTTPError 并且返回了 JSON errors，就透传
    from requests import HTTPError
    payload = {"error": str(err), "type": err.__class__.__name__}
    if isinstance(err, HTTPError) and err.response is not None:
        try:
            upstream = err.response.json()
            if "errors" in upstream.get("detail", {}):
                return jsonify(upstream), err.response.status_code
            payload["response"] = upstream
        except:
            payload["response_text"] = err.response.text[:500]
        payload["status_code"] = err.response.status_code
    return jsonify(payload), getattr(err, "code", 500)

# ------------------ 启动 ------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
