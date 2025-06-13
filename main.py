import os
import time
import logging
import traceback
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import requests

# === 配置（从环境变量读） ===
CLIENT_ID            = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET        = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN        = os.getenv("LOYVERSE_REFRESH_TOKEN")
REDIRECT_URI         = os.getenv("LOYVERSE_REDIRECT_URI")  # 仅在你想用 /callback 时需要
STORE_ID             = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

API_BASE        = "https://api.loyverse.com/v1.0"
OAUTH_TOKEN_URL = f"{API_BASE}/oauth/token"

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# 内存缓存
TOKEN_CACHE = {"access_token": None, "expires_at": 0}


def _refresh_access_token():
    """用环境变量里的 REFRESH_TOKEN 刷新 access_token"""
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("缺少 CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    TOKEN_CACHE["access_token"] = data["access_token"]
    # 提前 60s 过期
    TOKEN_CACHE["expires_at"] = time.time() + data.get("expires_in", 0) - 60


def get_access_token():
    if (TOKEN_CACHE["access_token"] is None
        or time.time() >= TOKEN_CACHE["expires_at"]
    ):
        _refresh_access_token()
    return TOKEN_CACHE["access_token"]


def loyverse_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def extract_phone_from_webhook(body):
    """从 VAPI Webhook Body 抽出来电号码"""
    # 1. call.customer.number
    phone = (
        body.get("call", {})
            .get("customer", {})
            .get("number")
    )
    # 2. 顶层 customer.number
    if not phone:
        phone = body.get("customer", {}).get("number")
    # 3. 前端直接传 phone
    if not phone:
        phone = body.get("phone")
    return phone or ""


# ------------------ 路由 ------------------

@app.route("/", methods=["GET"])
def home():
    return "Loyverse OAuth App is running."


@app.route("/get_menu", methods=["POST", "OPTIONS"])
def get_menu():
    if request.method == "OPTIONS":
        return "", 200

    items = []
    cursor = None
    while True:
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(f"{API_BASE}/items",
                            headers=loyverse_headers(),
                            params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        for it in data.get("items", []):
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if (str(s["store_id"]) == str(STORE_ID)
                            and s.get("available_for_sale")):
                        items.append({
                            "variant_id": v["variant_id"],
                            "item_name":  it["item_name"],
                            "category_id": it.get("category_id"),
                            "price_base": s["price"],
                        })
        cursor = data.get("cursor")
        if not cursor:
            break

    return jsonify({"menu": items})


@app.route("/get_customer", methods=["POST", "OPTIONS"])
def get_customer():
    if request.method == "OPTIONS":
        return "", 200

    body = request.get_json(silent=True) or {}
    phone = extract_phone_from_webhook(body)
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"limit": 1, "phone_number": phone},
        timeout=15
    )
    resp.raise_for_status()
    customers = resp.json().get("customers", [])
    if customers:
        c = customers[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})


@app.route("/create_customer", methods=["POST", "OPTIONS"])
def create_customer():
    if request.method == "OPTIONS":
        return "", 200

    body = request.get_json(silent=True) or {}
    name = body.get("name")
    phone = body.get("phone") or extract_phone_from_webhook(body)
    if not name or not phone:
        return jsonify({"error": "name & phone are required"}), 400

    payload = {"name": name, "phone_number": phone}
    resp = requests.post(f"{API_BASE}/customers",
                         headers=loyverse_headers(),
                         json=payload, timeout=15)
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json().get("id")})


@app.route("/place_order", methods=["POST", "OPTIONS"])
def place_order():
    if request.method == "OPTIONS":
        return "", 200

    body = request.get_json(silent=True) or {}
    items = body.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400

    # 1) 把 menu 拉下来，做个 price_map
    menu = get_menu().get_json()["menu"]
    price_map = {it["variant_id"]: it["price_base"] for it in menu}

    line_items = []
    total = 0
    for it in items:
        vid = it.get("variant_id")
        qty = int(it.get("quantity", 0))
        price = price_map.get(vid)
        if price is None:
            return jsonify({"error": f"unknown variant_id {vid}"}), 400
        line_items.append({
            "variant_id": vid,
            "quantity": qty,
            "price": price,
            "cost":  0
        })
        total += price * qty

    # 2) 支付方式（现金）
    payments = [{
        "payment_type_id": CASH_PAYMENT_TYPE_ID,
        "money_amount":    total,
        "type":            "CASH",
        "name":            "Cash",
        "paid_at":         datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }]

    # 3) 客户 ID（如果 webhook 提供的话）
    customer_id = body.get("customer_id") or extract_phone_from_webhook(body)

    payload = {
        "customer_id":   customer_id,
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items":    line_items,
        "payments":      payments,
    }
    resp = requests.post(f"{API_BASE}/receipts",
                         headers=loyverse_headers(),
                         json=payload, timeout=15)
    resp.raise_for_status()
    j = resp.json()
    return jsonify({
        "receipt_number": j.get("receipt_number"),
        "total_money":    j.get("total_money")
    })


# 全局错误捕获
@app.errorhandler(Exception)
def handle_exception(e):
    logging.error("Unhandled exception:", exc_info=e)
    resp = getattr(e, "response", None)
    payload = {"error": str(e)}
    if resp is not None:
        try:
            payload["detail"] = resp.json()
        except:
            payload["detail_text"] = resp.text
        return jsonify(payload), resp.status_code
    return jsonify(payload), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
