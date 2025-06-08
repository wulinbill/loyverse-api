import os
import json
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# =====================
# CONFIGURATION
# =====================

CONFIG_PATH = "tokens.json"

# 如果 tokens.json 存在，优先加载 token
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r") as f:
        token_data = json.load(f)
else:
    token_data = {
        "access_token": None,
        "refresh_token": None
    }

CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID") or ""
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET") or ""
REDIRECT_URI = os.getenv("LOYVERSE_REDIRECT_URI") or "https://loyverse-api.onrender.com/callback"

LOYVERSE_BASE = "https://api.loyverse.com/v1.0"


# =====================
# TOKEN REFRESH
# =====================

def refresh_token():
    if not token_data.get("refresh_token"):
        return False

    r = requests.post("https://api.loyverse.com/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": token_data["refresh_token"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    })
    if r.status_code == 200:
        new_tokens = r.json()
        token_data.update({
            "access_token": new_tokens["access_token"],
            "refresh_token": new_tokens.get("refresh_token", token_data["refresh_token"])
        })
        with open(CONFIG_PATH, "w") as f:
            json.dump(token_data, f)
        return True
    return False


# =====================
# AUTH HEADERS
# =====================

def get_headers():
    return {
        "Authorization": f"Bearer {token_data['access_token']}"
    }


# =====================
# ROUTES
# =====================

@app.route("/")
def index():
    return "✅ Loyverse API Server is Live!"

@app.route("/callback")
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "Missing code"

    res = requests.post("https://api.loyverse.com/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    })
    if res.status_code == 200:
        tokens = res.json()
        token_data.update({
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"]
        })
        with open(CONFIG_PATH, "w") as f:
            json.dump(token_data, f)
        return jsonify({"message": "Token received successfully", "tokens": tokens})
    else:
        return f"Failed to get token: {res.text}"


@app.route("/get_menu", methods=["POST"])
def get_menu():
    refresh_token()
    r = requests.get(f"{LOYVERSE_BASE}/items", headers=get_headers())
    if r.status_code != 200:
        return jsonify({"error": r.text}), 500
    items = r.json().get("items", [])
    menu = [{
        "sku": item["sku"],
        "name": item["name"],
        "category": item.get("category_id"),
        "price_base": item["variants"][0]["price"] if item.get("variants") else 0
    } for item in items]
    return jsonify(menu)


@app.route("/get_customer", methods=["POST"])
def get_customer():
    phone = request.json.get("phone")
    refresh_token()
    r = requests.get(f"{LOYVERSE_BASE}/customers?phone={phone}", headers=get_headers())
    if r.status_code == 200 and r.json().get("customers"):
        c = r.json()["customers"][0]
        return jsonify({"customer_id": c["id"], "name": c.get("name")})
    return jsonify({"customer_id": None, "name": None})


@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.json
    refresh_token()
    r = requests.post(f"{LOYVERSE_BASE}/customers", headers=get_headers(), json={
        "name": data["name"],
        "phone_number": data["phone"]
    })
    if r.status_code == 201:
        return jsonify({"customer_id": r.json()["id"]})
    return jsonify({"error": r.text}), 400


@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json
    refresh_token()
    payload = {
        "customer_id": data.get("customer_id"),
        "line_items": [
            {"variant_id": item["sku"], "quantity": item.get("qty", 1)}
            for item in data["items"]
        ]
    }
    r = requests.post(f"{LOYVERSE_BASE}/receipts", headers=get_headers(), json=payload)
    if r.status_code == 201:
        j = r.json()
        return jsonify({"total_with_tax": j["total_money"], "receipt_id": j["id"]})
    return jsonify({"error": r.text}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
