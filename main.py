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
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("REFRESH_TOKEN"),
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET")
    }

    r = requests.post(refresh_url, data=data)
    if r.status_code != 200:
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
    return "Loyverse API 接口在线"

@app.route("/get_menu", methods=["POST"])
def get_menu():
    r = requests.get("https://api.loyverse.com/v1.0/items", headers=loyverse_headers())
    return jsonify(r.json())

@app.route("/get_customer", methods=["POST"])
def get_customer():
    phone = request.json.get("phone")
    r = requests.get(f"https://api.loyverse.com/v1.0/customers?phone={phone}", headers=loyverse_headers())
    data = r.json()
    if data.get("customers"):
        return jsonify(data["customers"][0])
    return jsonify({"message": "not found"})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    body = {
        "name": request.json.get("name"),
        "phone_number": request.json.get("phone")
    }
    r = requests.post("https://api.loyverse.com/v1.0/customers", headers=loyverse_headers(), json=body)
    return jsonify(r.json())

@app.route("/place_order", methods=["POST"])
def place_order():
    customer_id = request.json.get("customer_id")
    items = request.json.get("items", [])
    if not customer_id or not items:
        return jsonify({"error": "customer_id and items are required"}), 400

    body = {
        "customer_id": customer_id,
        "line_items": [{"sku": i["sku"], "quantity": i["qty"]} for i in items]
    }

    r = requests.post("https://api.loyverse.com/v1.0/receipts", headers=loyverse_headers(), json=body)
    return jsonify(r.json())
