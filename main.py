import os
import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# 环境变量读取
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LOYVERSE_REDIRECT_URI") or "https://loyverse-api.onrender.com/callback"
SCOPES = "customers:read customers:write receipts:read receipts:write items:read"

# 授权页面重定向
@app.route("/authorize", methods=["GET"])
def authorize():
    url = (
        "https://api.loyverse.com/oauth/authorize?"
        f"response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&scope={SCOPES}"
    )
    return redirect(url)

# 回调接口处理授权码
@app.route("/callback", methods=["GET"])
def callback():
    code = request.args.get("code")
    if not code:
        return "Missing code", 400

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    r = requests.post("https://api.loyverse.com/oauth/token", data=data)
    return jsonify(r.json())

# 示例：菜单获取（需要 access_token）
@app.route("/get_menu", methods=["POST"])
def get_menu():
    token = request.headers.get("Authorization")  # Bearer xxxxx
    if not token:
        return jsonify({"error": "Missing Authorization header"}), 401

    res = requests.get(
        "https://api.loyverse.com/v1.0/items",
        headers={"Authorization": token}
    )
    return jsonify(res.json())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
