
import os
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 加载环境变量
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = "https://loyverse-api.onrender.com/callback"
TOKEN_URL = "https://api.loyverse.com/oauth/token"

TOKENS = {"access_token": None, "refresh_token": None}

@app.route("/")
def home():
    return "✅ Loyverse API Server is Live!"

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Error: missing authorization code", 400

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    response = requests.post(TOKEN_URL, data=data)
    if response.status_code == 200:
        token_data = response.json()
        TOKENS["access_token"] = token_data["access_token"]
        TOKENS["refresh_token"] = token_data.get("refresh_token")
        return jsonify({"message": "Token received successfully", "tokens": token_data})
    else:
        return f"Failed to get token: {response.text}", response.status_code

@app.route("/get_customer", methods=["POST"])
def get_customer():
    if not TOKENS["access_token"]:
        return "Token not available", 403

    phone = request.json.get("phone")
    url = f"https://api.loyverse.com/v1.0/customers?phone={phone}"
    headers = {"Authorization": f"Bearer {TOKENS['access_token']}"}
    response = requests.get(url, headers=headers)
    return jsonify(response.json()), response.status_code

@app.route("/get_menu", methods=["POST"])
def get_menu():
    if not TOKENS["access_token"]:
        return "Token not available", 403

    url = "https://api.loyverse.com/v1.0/items"
    headers = {"Authorization": f"Bearer {TOKENS['access_token']}"}
    response = requests.get(url, headers=headers)
    return jsonify(response.json()), response.status_code
