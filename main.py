import os
import time
import logging
from typing import List, Dict, Any

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

# 基础配置
LOYVERSE_API_BASE = "https://api.loyverse.com/v1.0"
OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    raise RuntimeError("请在环境变量中设置 CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN")

# 简单缓存 token 及过期时间
_token_cache: Dict[str, Any] = {}

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_access_token() -> str:
    """获取并缓存 OAuth2 access_token，自动处理过期刷新"""
    now = int(time.time())
    token_info = _token_cache.get("token_info", {})

    if token_info and token_info.get("expiry", 0) > now + 60:
        return token_info["access_token"]

    # 刷新 token
    data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(OAUTH_TOKEN_URL, headers=headers, data=data)
    if resp.status_code != 200:
        logger.error("Token 刷新失败 %s %s", resp.status_code, resp.text)
        raise RuntimeError("无法刷新 Loyverse 访问令牌")
    result = resp.json()
    access_token = result["access_token"]
    expires_in = result.get("expires_in", 3600)
    # 缓存，并记录过期时间
    _token_cache["token_info"] = {
        "access_token": access_token,
        "expiry": now + expires_in
    }
    return access_token

def loyverse_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json"
    }

def fetch_all_items() -> List[Dict[str, Any]]:
    """自动分页获取所有 items"""
    items: List[Dict[str, Any]] = []
    url = f"{LOYVERSE_API_BASE}/items"
    params = {"limit": 250}
    while True:
        resp = requests.get(url, headers=loyverse_headers(), params=params)
        if resp.status_code != 200:
            raise RuntimeError(f"获取菜单失败: {resp.status_code} {resp.text}")
        data = resp.json()
        batch = data.get("items", [])
        items.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return items

# Flask 应用
app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return "Loyverse API 服务运行中"

@app.route("/get_menu", methods=["POST"])
def get_menu():
    try:
        items = fetch_all_items()
        # 只返回关键信息
        simplified = [
            {
                "id": it["id"],
                "name": it["item_name"],
                "sku": [v["variant_id"] for v in it.get("variants", [])],
                "price": [v["stores"][0]["price"] for v in it.get("variants", [])],
            }
            for it in items
        ]
        return jsonify({"items": simplified})
    except Exception as e:
        logger.exception("获取菜单出错")
        return jsonify({"error": str(e)}), 500

@app.route("/get_customer", methods=["POST"])
def get_customer():
    data = request.get_json(force=True)
    phone = data.get("phone", "")
    resp = requests.get(f"{LOYVERSE_API_BASE}/customers", headers=loyverse_headers(), params={"phone": phone})
    if resp.status_code != 200:
        return jsonify({"error": "lookup_failed", "details": resp.text}), resp.status_code
    custs = resp.json().get("customers", [])
    if not custs:
        return jsonify({"customer_id": None, "name": None})
    c = custs[0]
    return jsonify({"customer_id": c["id"], "name": c["name"]})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.get_json(force=True)
    payload = {
        "name": data.get("name"),
        "phone_number": data.get("phone")
    }
    resp = requests.post(f"{LOYVERSE_API_BASE}/customers", headers=loyverse_headers(), json=payload)
    if resp.status_code not in (200, 201):
        return jsonify({"error": "create_failed", "details": resp.text}), resp.status_code
    return jsonify({"customer_id": resp.json().get("id")})

@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.get_json(force=True)
    customer_id = data["customer_id"]
    items = data["items"]
    body = {
        "customer_id": customer_id,
        "line_items": [
            {"item_variation_id": item["sku"], "quantity": item["qty"]}
            for item in items
        ]
    }
    resp = requests.post(f"{LOYVERSE_API_BASE}/receipts", headers=loyverse_headers(), json=body)
    if resp.status_code not in (200, 201):
        return jsonify({"error": "order_failed", "details": resp.text}), resp.status_code
    return jsonify(resp.json()), resp.status_code

if __name__ == "__main__":
    # 本地调试用
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
