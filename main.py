
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import os
import requests

app = Flask(__name__)
CORS(app)

# 环境变量
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = "https://loyverse-api.onrender.com/callback"

# Token 缓存（演示用途，建议实际部署时保存到数据库或文件中）
TOKEN_STORE = {}

@app.route("/")
def index():
    return "✅ Loyverse API Server is Live!"

@app.route("/authorize")
def authorize():
    scope = "products.read customers.read receipts.write"
    url = (
        f"https://api.loyverse.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope}"
    )
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Missing code", 400

    token_url = "https://api.loyverse.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }

    r = requests.post(token_url, json=data)
    if r.status_code != 200:
        return f"Failed to get token: {r.text}", 500

    token_data = r.json()
    TOKEN_STORE["access_token"] = token_data["access_token"]
    TOKEN_STORE["refresh_token"] = token_data.get("refresh_token")
    return jsonify({"message": "✅ Token saved!", "token_info": token_data})

@app.route("/get_menu", methods=["POST"])
def get_menu():
    access_token = TOKEN_STORE.get("access_token")
    if not access_token:
        return jsonify({"error": "Not authorized yet."}), 403

    url = "https://api.loyverse.com/v1.0/items"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, headers=headers)

    if r.status_code != 200:
        return jsonify({"error": r.text}), r.status_code

    return jsonify(r.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
