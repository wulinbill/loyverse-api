import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

TOKEN_CACHE = {}

def get_token():
    if "access_token" in TOKEN_CACHE:
        return TOKEN_CACHE["access_token"]

    refresh_url = "https://api.loyverse.com/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("REFRESH_TOKEN"),
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET")
    }

    r = requests.post(refresh_url, headers=headers, data=data)
    if r.status_code != 200:
        print("[ERROR] Token refresh failed:", r.status_code, r.text)
        raise Exception("Failed to refresh token")

    token_data = r.json()
    TOKEN_CACHE["access_token"] = token_data["access_token"]
    return token_data["access_token"]

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }

@app.route("/")
def index():
    return "Loyverse API is running."

@app.route("/get_menu", methods=["POST"])
def get_menu():
    r = requests.get("https://api.loyverse.com/v1.0/items", headers=loyverse_headers())
    return jsonify(r.json()), r.status_code

@app.route("/get_customer", methods=["POST"])
def get_customer():
    data = request.json
    phone = data.get("phone", "")
    r = requests.get(f"https://api.loyverse.com/v1.0/customers?phone={phone}", headers=loyverse_headers())
    customers = r.json().get("customers", [])
    if customers:
        return jsonify({"customer_id": customers[0]["id"], "name": customers[0]["name"]})
    return jsonify({"customer_id": None, "name": None})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.json
    payload = {
        "name": data.get("name"),
        "phone_number": data.get("phone")
    }
    r = requests.post("https://api.loyverse.com/v1.0/customers", headers=loyverse_headers(), json=payload)
    if r.status_code == 201:
        return jsonify({"customer_id": r.json()["id"]})
    return jsonify({"error": "create_failed", "details": r.text}), r.status_code

@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json
    customer_id = data["customer_id"]
    items = data["items"]
    body = {
        "customer_id": customer_id,
        "line_items": [{"item_variation_id": item["sku"], "quantity": item["qty"]} for item in items]
    }
    r = requests.post("https://api.loyverse.com/v1.0/receipts", headers=loyverse_headers(), json=body)
    return jsonify(r.json()), r.status_code
