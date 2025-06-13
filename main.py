# main.py

import os
from flask import Flask, request, redirect, jsonify
import requests

app = Flask(__name__)

# 从环境变量读取凭据和回调地址
CLIENT_ID     = os.getenv("LOYVERSE_CLIENT_ID", "GjfBWi8E7o48tuJwYgkQ")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET", "B7z26sykAC2_zn5qsuiWW6FErXxd0zn4M0-Hgr6e9xCw0rJzbo7iCQ==")
REDIRECT_URI  = os.getenv("LOYVERSE_REDIRECT_URI", "https://loyverse-api.onrender.com/callback")
SCOPE         = os.getenv("LOYVERSE_SCOPE", "CUSTOMERS_READ CUSTOMERS_WRITE ITEMS_READ MERCHANT_READ PAYMENT_TYPES_READ POS_DEVICES_READ POS_DEVICES_WRITE RECEIPTS_READ RECEIPTS_WRITE STORES_READ TAXES_READ TAXES_WRITE")

AUTH_URL  = "https://api.loyverse.com/oauth/authorize"
TOKEN_URL = "https://api.loyverse.com/oauth/token"

@app.route("/authorize")
def authorize():
    """
    引导用户跳转到 Loyverse 授权页面。
    用户同意后，Loyverse 会重定向到 /callback?code=...
    """
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }
    url = AUTH_URL + "?" + "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return redirect(url)

@app.route("/callback")
def callback():
    """
    授权回调：接收 code，向 Loyverse 换取 access_token 和 refresh_token。
    返回 JSON 格式的 tokens，或记录到持久化存储中。
    """
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "missing_code"}), 400

    payload = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(TOKEN_URL, data=payload)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        return jsonify({
            "error": "token_exchange_failed",
            "details": resp.text
        }), resp.status_code

    tokens = resp.json()
    # 这里可以把 tokens 写入数据库或 KV 存储，方便后续调用 loyverse_headers()
    return jsonify(tokens)

if __name__ == "__main__":
    # Render 上通常使用环境变量 PORT
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
