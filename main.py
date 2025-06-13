import os
import time
import logging
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# === Environment Variables ===
CLIENT_ID            = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET        = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN        = os.getenv("LOYVERSE_REFRESH_TOKEN")
STORE_ID             = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

# === Endpoints ===
API_BASE        = "https://api.loyverse.com/v1.0"
OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# In-memory token cache
token_cache = {"access_token": None, "expires_at": 0}


def _refresh_access_token():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("Missing CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    token_cache["access_token"] = data["access_token"]
    token_cache["expires_at"]   = time.time() + data.get("expires_in", 0) - 60


def get_access_token():
    if token_cache["access_token"] is None or time.time() >= token_cache["expires_at"]:
        _refresh_access_token()
    return token_cache["access_token"]


def loyverse_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def extract_phone(webhook_body: dict) -> str:
    phone = webhook_body.get("call", {}).get("customer", {}).get("number")
    if not phone:
        phone = webhook_body.get("customer", {}).get("number")
    if not phone:
        phone = webhook_body.get("phone")
    return phone or ""


def ensure_customer_by_phone(phone: str):
    # Try to find existing customer
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 1}, timeout=15
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        return custs[0]["id"]
    # Create new customer
    payload = {"name": phone, "phone_number": phone}
    resp2 = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json=payload, timeout=15
    )
    resp2.raise_for_status()
    return resp2.json().get("id")


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
        resp = requests.get(
            f"{API_BASE}/items",
            headers=loyverse_headers(),
            params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if str(s.get("store_id")) == str(STORE_ID) and s.get("available_for_sale"):
                        items.append({
                            "variant_id": v["variant_id"],
                            "item_name":  it["item_name"],
                            "category_id": it.get("category_id"),
                            "price_base": s.get("price"),
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
    phone = extract_phone(body)
    if not phone:
        return jsonify({"error": "phone is required"}), 400
    cust_id = ensure_customer_by_phone(phone)
    return jsonify({"customer_id": cust_id})


@app.route("/create_customer", methods=["POST", "OPTIONS"])
def create_customer():
    if request.method == "OPTIONS":
        return "", 200
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    phone = body.get("phone")
    if not name or not phone:
        return jsonify({"error": "name & phone are required"}), 400
    payload = {"name": name, "phone_number": phone}
    resp = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json=payload, timeout=15
    )
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json().get("id")})


@app.route("/place_order", methods=["POST", "OPTIONS"])
def place_order():
    if request.method == "OPTIONS":
        return "", 200
    body = request.get_json(silent=True) or {}
    orders = body.get("items", [])
    if not orders:
        return jsonify({"error": "items array is required"}), 400
    # Build price map
    menu_items = get_menu().get_json().get("menu", [])
    price_map = {it["variant_id"]: it["price_base"] for it in menu_items}
    line_items = []
    total = 0
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
        total += price * qty
    # Determine customer_id
    cust_id = body.get("customer_id")
    if not cust_id:
        phone = extract_phone(body)
        if phone:
            cust_id = ensure_customer_by_phone(phone)
    # Build payments
    payments = [{
        "payment_type_id": CASH_PAYMENT_TYPE_ID,
        "money_amount":    total,
        "type":            "CASH",
        "name":            "Cash",
        "paid_at":         datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    }]
    payload = {
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items":    line_items,
        "payments":      payments,
    }
    if cust_id:
        payload["customer_id"] = cust_id
    resp = requests.post(
        f"{API_BASE}/receipts",
        headers=loyverse_headers(),
        json=payload, timeout=15
    )
    resp.raise_for_status()
    r = resp.json()
    return jsonify({
        "receipt_number": r.get("receipt_number"),
        "total_money":    r.get("total_money")
    })


@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception", exc_info=err)
    resp = getattr(err, 'response', None)
    payload = {"error": str(err)}
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
