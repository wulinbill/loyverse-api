import os
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import logging
import traceback

load_dotenv()

# 配置简单日志，便于 Render / Vercel 诊断
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

# 环境变量中必须设置：
# LOYVERSE_CLIENT_ID, LOYVERSE_CLIENT_SECRET, LOYVERSE_REFRESH_TOKEN, LOYVERSE_STORE_ID
CLIENT_ID       = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET   = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN   = os.getenv("LOYVERSE_REFRESH_TOKEN")
STORE_ID        = os.getenv("LOYVERSE_STORE_ID")
API_BASE        = "https://api.loyverse.com/v1.0"

# 简单缓存 access_token
TOKEN_CACHE = {"token": None, "expires_at": 0}

def refresh_access_token():
    """通过 refresh_token 获取新的 access_token，并缓存"""
    url = f"{API_BASE}/oauth/token"
    resp = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET
        }
    )
    resp.raise_for_status()
    data = resp.json()
    TOKEN_CACHE["token"]       = data["access_token"]
    # 提前 60s 刷新
    TOKEN_CACHE["expires_at"]  = time.time() + data["expires_in"] - 60

def get_access_token():
    """返回当前有效的 access_token"""
    if TOKEN_CACHE["token"] is None or time.time() >= TOKEN_CACHE["expires_at"]:
        refresh_access_token()
    return TOKEN_CACHE["token"]

def loyverse_headers(content_type="application/json"):
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": content_type
    }

@app.route("/")
def index():
    return "Loyverse API proxy is running."

@app.route("/get_menu", methods=["POST"])
def get_menu():
    """拉取全量商品，并筛选出本店可售价格"""
    items = []
    cursor = None
    while True:
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{API_BASE}/items", headers=loyverse_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            # 只保留本店 store_id 的变体价格
            variants = []
            for v in it.get("variants", []):
                # v["stores"] 是个列表，包含每个店的信息
                for s in v.get("stores", []):
                    if str(s.get("store_id")) == str(STORE_ID) and s.get("available_for_sale"):
                        variants.append({
                            "variant_id": v["variant_id"],
                            "price":      s["price"]
                        })
            if not variants:
                continue
            items.append({
                "sku":        variants[0]["variant_id"],
                "name":       it["item_name"],
                "category":   it.get("category_id"),
                "price_base": variants[0]["price"],
                # aliases 逻辑可在这里增补
                "aliases":    []
            })
        cursor = data.get("cursor")
        if not cursor:
            break

    return jsonify({"menu": items})

@app.route("/get_customer", methods=["POST"])
def get_customer():
    """通过 phone_number 查询客户"""
    phone = request.json.get("phone", "")
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 250}
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        c = custs[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    """新建客户"""
    data = request.json
    payload = {
        "name":         data["name"],
        "phone_number": data["phone"]
    }
    resp = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json=payload
    )
    resp.raise_for_status()
    c = resp.json()
    return jsonify({"customer_id": c["id"]})

@app.route("/place_order", methods=["POST"])
def place_order():
    """在 Loyverse POS 创建 TAKEAWAY 单，并返回 receipt_number 与 total_money"""
    data = request.json
    items = data["items"]

    # 组装符合官方文档的请求体
    body = {
        "customer_id": data.get("customer_id"),
        "store_id":    STORE_ID,
        # 使用 dining_option 指示外带，官方已弃用 order_type
        "dining_option": "TAKEAWAY",
        "line_items": [
            {"variant_id": it["variant_id"], "quantity": it["quantity"]}
            for it in items
        ]
    }

    resp = requests.post(
        f"{API_BASE}/receipts",
        headers=loyverse_headers(),
        json=body
    )
    resp.raise_for_status()
    r = resp.json()

    return jsonify({
        "receipt_number": r.get("receipt_number"),
        "total_money":    r.get("total_money")
    })

# ---------------------- 全局异常处理 ---------------------- #

@app.errorhandler(Exception)
def handle_exception(err):
    """捕获未处理异常并返回 JSON，同时打印堆栈方便排查"""
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    return jsonify({
        "error": str(err),
        "type": err.__class__.__name__
    }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
