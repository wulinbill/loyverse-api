# main.py
from flask import Flask, request, jsonify, redirect
import os, requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# OAuth2 Credentials
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LOYVERSE_REDIRECT_URI")
TOKEN_URL = "https://api.loyverse.com/oauth/token"
AUTH_URL = "https://api.loyverse.com/oauth/authorize"
API_BASE = "https://api.loyverse.com/v1.0"

# Store tokens (for demo; use DB or cache in production)
ACCESS_TOKEN = None
REFRESH_TOKEN = None

@app.route("/")
def index():
    return "Loyverse OAuth API is running."

@app.route("/authorize")
def authorize():
    return redirect(
        f"{AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope=offline_access"
    )

@app.route("/callback")
def callback():
    global ACCESS_TOKEN, REFRESH_TOKEN
    code = request.args.get("code")
    if not code:
        return "No code provided.", 400

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    resp = requests.post(TOKEN_URL, data=data)
    if resp.status_code != 200:
        return f"Failed to get token: {resp.text}", 400

    tokens = resp.json()
    ACCESS_TOKEN = tokens.get("access_token")
    REFRESH_TOKEN = tokens.get("refresh_token")
    return jsonify(tokens)

@app.route("/get_menu", methods=["POST"])
def get_menu():
    if not ACCESS_TOKEN:
        return "Unauthorized", 401

    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    items_resp = requests.get(f"{API_BASE}/items", headers=headers)
    if items_resp.status_code != 200:
        return f"Error fetching menu: {items_resp.text}", 500

    items = items_resp.json().get("items", [])
    result = []
    for it in items:
        result.append({
            "sku": it.get("sku"),
            "nombre": it.get("name"),
            "precio_base": it.get("price"),
            "categoria": it.get("category_id"),
            "aliases": []  # optional logic
        })
    return jsonify(result)

@app.route("/create_customer", methods=["POST"])
def create_customer():
    global ACCESS_TOKEN
    data = request.get_json()
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        return "Missing data", 400

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": name,
        "phone_number": phone
    }
    r = requests.post(f"{API_BASE}/customers", json=payload, headers=headers)
    return r.json(), r.status_code

@app.route("/place_order", methods=["POST"])
def place_order():
    global ACCESS_TOKEN
    data = request.get_json()
    customer_id = data.get("customer_id")
    items = data.get("items")

    if not customer_id or not items:
        return "Missing customer or items", 400

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "customer_id": customer_id,
        "line_items": [
            {"sku": i["sku"], "quantity": i.get("qty", 1)} for i in items
        ]
    }
    r = requests.post(f"{API_BASE}/receipts", json=payload, headers=headers)
    return r.json(), r.status_code

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
