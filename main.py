import os
import logging
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# ① 开启 CORS，允许所有域名跨域请求
CORS(app)

API_BASE = "https://api.loyverse.com/v1.0"
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("LOYVERSE_REFRESH_TOKEN")
STORE_ID = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

_access_token = None

def loyverse_headers():
    global _access_token
    if not _access_token:
        _oauth_refresh()
    return {
        "Authorization": f"Bearer {_access_token}",
        "Content-Type": "application/json"
    }

def _oauth_refresh():
    global _access_token
    resp = requests.post(
        f"{API_BASE}/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    _access_token = data["access_token"]
    # 如果你想持久化新的 refresh_token，也可以在这里写入 .env
    # new_rt = data.get("refresh_token")

@app.route("/", methods=["GET"])
def index():
    return jsonify({"message": "Loyverse OAuth app is running"}), 200

@app.route("/get_menu", methods=["POST"])
def get_menu():
    items = []
    cursor = None
    while True:
        params = {"limit": 250, "cursor": cursor} if cursor else {"limit": 250}
        resp = requests.get(f"{API_BASE}/items", headers=loyverse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            variants = [
                {"variant_id": v["variant_id"], "price": s["price"]}
                for v in it.get("variants", [])
                for s in v.get("stores", [])
                if str(s["store_id"]) == str(STORE_ID) and s.get("available_for_sale")
            ]
            if variants:
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

@app.route("/get_customer", methods=["POST"])
def get_customer():
    body = request.get_json(force=True)
    phone = body.get("phone", "")
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 50},
        timeout=15,
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        c = custs[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.get_json(force=True)
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        return jsonify({"error": "name & phone are required"}), 400
    resp = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json={"name": name, "phone_number": phone},
        timeout=15,
    )
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json()["id"]})

@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.get_json(force=True)
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400

    body = {
        "customer_id": customer_id,
        "store_id": STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items": [{"variant_id": it["variant_id"], "quantity": it["quantity"]} for it in items],
        # ② 只保留必需的 payments 字段
        "payments": [{
            "payment_type_id": CASH_PAYMENT_TYPE_ID,
            "money_amount": sum(it["quantity"] * it.get("price_base", 0) for it in items)
        }]
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=body, timeout=15)
    resp.raise_for_status()
    r = resp.json()
    return jsonify({"receipt_number": r.get("receipt_number"), "total_money": r.get("total_money")})

@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    tb = traceback.format_exc()
    return jsonify({"error": str(err), "traceback": tb}), getattr(err, "code", 500)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
