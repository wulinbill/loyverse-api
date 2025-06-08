import os
import json
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)

ACCESS_TOKEN = None
ACCESS_TOKEN_EXPIRES = 0

def get_token():
    global ACCESS_TOKEN, ACCESS_TOKEN_EXPIRES
    now = int(time.time())

    if ACCESS_TOKEN and now < ACCESS_TOKEN_EXPIRES - 60:
        return ACCESS_TOKEN

    # refresh token
    res = requests.post("https://api.loyverse.com/token", data={
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("LOYVERSE_REFRESH_TOKEN"),
        "client_id": os.getenv("LOYVERSE_CLIENT_ID"),
        "client_secret": os.getenv("LOYVERSE_CLIENT_SECRET")
    })

    if res.status_code == 200:
        data = res.json()
        ACCESS_TOKEN = data["access_token"]
        ACCESS_TOKEN_EXPIRES = now + int(data["expires_in"])
        return ACCESS_TOKEN
    else:
        raise Exception("Failed to refresh token")

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }

@app.route('/')
def home():
    return '✅ Loyverse API is Live!'

@app.route('/callback')
def oauth_callback():
    return "🔗 Callback received! You can close this page."

@app.route('/get_menu', methods=['POST'])
def get_menu():
    r = requests.get("https://api.loyverse.com/v1.0/items", headers=loyverse_headers())
    return jsonify(r.json())

@app.route('/get_customer', methods=['POST'])
def get_customer():
    phone = request.json.get("phone")
    r = requests.get(f"https://api.loyverse.com/v1.0/customers?phone={phone}", headers=loyverse_headers())
    data = r.json()
    if "customers" in data and data["customers"]:
        customer = data["customers"][0]
        return jsonify({
            "customer_id": customer["id"],
            "name": customer.get("name", "")
        })
    else:
        return jsonify({"customer_id": None, "name": None})

@app.route('/create_customer', methods=['POST'])
def create_customer():
    name = request.json.get("name")
    phone = request.json.get("phone")
    r = requests.post("https://api.loyverse.com/v1.0/customers", headers=loyverse_headers(), json={
        "name": name,
        "phone_number": phone
    })
    if r.status_code == 201:
        return jsonify({"customer_id": r.json().get("id")})
    else:
        return jsonify({"error": r.text}), r.status_code

@app.route('/place_order', methods=['POST'])
def place_order():
    customer_id = request.json.get("customer_id")
    items = request.json.get("items")

    body = {
        "line_items": [
            {"item_id": item["sku"], "quantity": item["qty"]} for item in items
        ]
    }

    if customer_id:
        body["customer_id"] = customer_id

    r = requests.post("https://api.loyverse.com/v1.0/receipts", headers=loyverse_headers(), json=body)

    if r.status_code == 201:
        data = r.json()
        return jsonify({
            "receipt_id": data.get("receipt_number"),
            "total_with_tax": data.get("total_money", {}).get("amount", 0) / 100
        })
    else:
        return jsonify({"error": r.text}), r.status_code

# Run locally for testing
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
