import os
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, STORE_ID)
load_dotenv()

app = Flask(__name__)
CORS(app)

TOKEN_CACHE = {}
API_BASE = "https://api.loyverse.com/v1.0"


def get_token():
    # Return cached access token if still valid
    if TOKEN_CACHE.get("access_token"):
        return TOKEN_CACHE["access_token"]

    url = f"{API_BASE}/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("REFRESH_TOKEN"),
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
    }
    resp = requests.post(url, headers=headers, data=data)
    resp.raise_for_status()
    token_data = resp.json()
    TOKEN_CACHE["access_token"] = token_data["access_token"]
    return token_data["access_token"]


def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }


@app.route("/", methods=["GET"])
def health():
    return "Loyverse API Proxy running."


@app.route("/get_menu", methods=["GET"])
def get_menu():
    """
    Fetch all items and their variants from Loyverse
    """
    url = f"{API_BASE}/items?limit=250"
    resp = requests.get(url, headers=loyverse_headers())
    resp.raise_for_status()
    return jsonify(resp.json())


@app.route("/get_customer", methods=["POST"])
def get_customer():
    payload = request.json or {}
    phone = payload.get("phone")
    if not phone:
        return jsonify({"customer_id": None, "name": None}), 400

    url = f"{API_BASE}/customers?phone_number={phone}"
    resp = requests.get(url, headers=loyverse_headers())
    resp.raise_for_status()
    data = resp.json().get("customers", [])
    if data:
        cust = data[0]
        return jsonify({"customer_id": cust["id"], "name": cust.get("name")})
    return jsonify({"customer_id": None, "name": None})


@app.route("/create_customer", methods=["POST"])
def create_customer():
    payload = request.json or {}
    name = payload.get("name")
    phone = payload.get("phone")
    if not name or not phone:
        return jsonify({"error": "missing_fields"}), 400

    body = {"name": name, "phone_number": phone}
    url = f"{API_BASE}/customers"
    resp = requests.post(url, headers=loyverse_headers(), json=body)
    if resp.status_code in (200, 201):
        return jsonify({"customer_id": resp.json().get("id")})
    return jsonify({"error": "create_failed", "details": resp.text}), resp.status_code


@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json or {}
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    store_id = os.getenv("STORE_ID")

    if not store_id:
        return jsonify({"error": "missing_store_id"}), 500
    if not items:
        return jsonify({"error": "no_items"}), 400

    line_items = []
    for it in items:
        sku = it.get("sku")
        qty = it.get("qty", 1)
        if not sku:
            continue
        line_items.append({
            "item_variation_id": sku,
            "quantity": qty
        })

    body = {
        "store_id": store_id,
        "customer_id": customer_id,
        "line_items": line_items
    }
    url = f"{API_BASE}/receipts"
    resp = requests.post(url, headers=loyverse_headers(), json=body)
    if resp.status_code in (200, 201):
        return jsonify(resp.json()), resp.status_code
    return jsonify({"error": "order_failed", "details": resp.text}), resp.status_code


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
