import os
import logging
from functools import wraps

import requests
from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# —— 基本配置 —— #
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

TOKEN_CACHE = {}

# —— 装饰器：统一捕获异常并返回 JSON 错误 —— #
def json_endpoint(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except requests.RequestException as e:
            logger.exception("网络请求失败")
            return jsonify({"error": "external_request_failed", "details": str(e)}), 502
        except Exception as e:
            logger.exception("内部错误")
            return jsonify({"error": "internal_error", "details": str(e)}), 500
    return decorated

# —— OAuth2 Token 刷新与缓存 —— #
def get_token():
    """先从缓存取；否则用 refresh_token 刷新并缓存。"""
    token = TOKEN_CACHE.get("access_token")
    if token:
        return token

    refresh_url = "https://api.loyverse.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("REFRESH_TOKEN"),
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET")
    }
    logger.info("Refreshing access token…")
    r = requests.post(refresh_url,
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      data=payload,
                      timeout=10)
    if r.status_code != 200:
        logger.error("Token refresh failed: %s %s", r.status_code, r.text)
        raise Exception(f"Token refresh failed ({r.status_code})")
    data = r.json()
    TOKEN_CACHE["access_token"] = data["access_token"]
    logger.info("Obtained new access token, expires in %s seconds", data.get("expires_in"))
    return data["access_token"]

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }

# —— 根路由 —— #
@app.route("/", methods=["GET"])
def index():
    return "Loyverse API service is up."

# —— 1. 获取菜单 —— #
@app.route("/get_menu", methods=["POST"])
@json_endpoint
def get_menu():
    """调用 Loyverse /items，返回 items 列表。"""
    r = requests.get("https://api.loyverse.com/v1.0/items",
                     headers=loyverse_headers(), timeout=10)
    r.raise_for_status()
    data = r.json().get("items", [])
    # 只抽取我们需要的字段，供 AI 识别
    slim = [{
        "sku": item["variants"][0]["variant_id"],
        "name": item["item_name"],
        "category": item.get("category_id"),
        "price": item["variants"][0]["stores"][0]["price"]
    } for item in data]
    return jsonify({"items": slim})

# —— 2. 查客户 —— #
@app.route("/get_customer", methods=["POST"])
@json_endpoint
def get_customer():
    phone = request.json.get("phone", "").strip()
    if not phone:
        return jsonify({"customer_id": None, "name": None}), 400

    r = requests.get(f"https://api.loyverse.com/v1.0/customers?phone_number={phone}",
                     headers=loyverse_headers(), timeout=10)
    r.raise_for_status()
    custs = r.json().get("customers", [])
    if not custs:
        return jsonify({"customer_id": None, "name": None})
    c = custs[0]
    return jsonify({"customer_id": c["id"], "name": c["name"]})

# —— 3. 新建客户 —— #
@app.route("/create_customer", methods=["POST"])
@json_endpoint
def create_customer():
    body = {
        "name": request.json.get("name"),
        "phone_number": request.json.get("phone")
    }
    r = requests.post("https://api.loyverse.com/v1.0/customers",
                      headers=loyverse_headers(), json=body, timeout=10)
    if r.status_code not in (200, 201):
        logger.error("create_customer failed: %s %s", r.status_code, r.text)
        return jsonify({"error": "create_failed", "details": r.text}), r.status_code
    c = r.json()
    return jsonify({"customer_id": c["id"]})

# —— 4. 下单 —— #
@app.route("/place_order", methods=["POST"])
@json_endpoint
def place_order():
    data = request.json
    items = data.get("items", [])
    customer_id = data.get("customer_id")
    if not items:
        return jsonify({"error": "no_items"}), 400

    line_items = [{
        "item_variation_id": it["sku"],
        "quantity": it["qty"]
    } for it in items]

    payload = {"line_items": line_items}
    if customer_id:
        payload["customer_id"] = customer_id

    r = requests.post("https://api.loyverse.com/v1.0/receipts",
                      headers=loyverse_headers(), json=payload, timeout=10)
    r.raise_for_status()
    receipt = r.json()

    # 计算总价（含税）
    total = receipt.get("total_with_tax") or receipt.get("total")
    return jsonify({
        "receipt_id": receipt.get("id"),
        "total_with_tax": total
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
