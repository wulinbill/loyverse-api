import os
import time
import logging
import traceback
import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

# === 配置项（Environment Variables） ===
CLIENT_ID     = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("LOYVERSE_REFRESH_TOKEN")
REDIRECT_URI  = os.getenv("LOYVERSE_REDIRECT_URI")
STORE_ID      = os.getenv("LOYVERSE_STORE_ID")

# === 常量 ===
OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"
API_BASE        = "https://api.loyverse.com/v1.0"

# === Flask 应用 & CORS ===
app = Flask(__name__)
CORS(app)  # ← 允许所有路由、所有源的跨域请求

logging.basicConfig(level=logging.INFO)

# ==== 内存缓存 Access Token ====
TOKEN_CACHE = {"access_token": None, "expires_at": 0}

def _oauth_refresh():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("缺少 CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    TOKEN_CACHE["access_token"] = data["access_token"]
    TOKEN_CACHE["expires_at"]   = time.time() + data.get("expires_in", 0) - 60

def get_access_token():
    if (TOKEN_CACHE["access_token"] is None
        or time.time() >= TOKEN_CACHE["expires_at"]
    ):
        _oauth_refresh()
    return TOKEN_CACHE["access_token"]

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type":  "application/json",
    }

# ---------- 首页 ----------
@app.route("/")
def index():
    if not all([CLIENT_ID, REDIRECT_URI]):
        return "<h3>请先配置 LOYVERSE_CLIENT_ID 与 LOYVERSE_REDIRECT_URI</h3>", 400

    scopes = [
        "stores.read", "customers.read", "customers.write",
        "items.read", "receipts.read", "receipts.write",
    ]
    auth_url = (
        "https://api.loyverse.com/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={'%20'.join(scopes)}"
    )
    return (
        "<h2>Loyverse OAuth Demo</h2>"
        "<p>点击下方链接完成授权：</p>"
        f"<p><a href='{auth_url}'>🔗 Connect Loyverse</a></p>"
    )

# ---------- 回调 ----------
def handle_callback():
    code = request.args.get("code")
    if not code:
        return "缺少 ?code= 参数", 400
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return f"Token 请求失败：{resp.status_code} - {resp.text}", resp.status_code
    tok = resp.json()
    return render_template_string(
        """
        <h2>✅ 授权成功</h2>
        <p><strong>Access Token:</strong> {{access}}</p>
        <p><strong>Refresh Token:</strong> {{refresh}}</p>
        <hr><p style="color:red;">
          ⚠️ 请立即复制并安全保存 Refresh Token，页面刷新后无法再次查看。
        </p>
        """,
        access=tok["access_token"],
        refresh=tok["refresh_token"],
    )

@app.route("/oauth/callback")
@app.route("/callback")
def oauth_callback():
    return handle_callback()

# ---------- 获取菜单 ----------
@app.route("/get_menu", methods=["POST"])
def get_menu():
    items = []
    cursor = None
    while True:
        params = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{API_BASE}/items", headers=loyverse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            variants = []
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if str(s["store_id"]) == str(STORE_ID) and s.get("available_for_sale"):
                        variants.append({"variant_id": v["variant_id"], "price": s["price"]})
            if not variants:
                continue
            items.append({
                "sku": variants[0]["variant_id"],
                "name": it["item_name"],
                "category": it.get("category_id"),
                "price_base": variants[0]["price"],
                "aliases": [],
            })
        cursor = data.get("cursor")
        if not cursor:
            break
    return jsonify({"menu": items})

# ---------- 查询顾客 ----------
@app.route("/get_customer", methods=["POST"])
def get_customer():
    phone = request.json.get("phone", "")
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 50},
        timeout=15,
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        c = custs[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})

# ---------- 创建顾客 ----------
@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.json or {}
    if "name" not in data or "phone" not in data:
        return jsonify({"error": "name & phone are required"}), 400
    payload = {"name": data["name"], "phone_number": data["phone"]}
    resp = requests.post(f"{API_BASE}/customers", headers=loyverse_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json()["id"]})

# ---------- 下单 ----------
@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400
    body = {
        "customer_id":   data.get("customer_id"),
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items": [
            {"variant_id": it["variant_id"], "quantity": it["quantity"]}
            for it in items
        ],
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=body, timeout=15)
    resp.raise_for_status()
    r = resp.json()
    return jsonify({"receipt_number": r.get("receipt_number"), "total_money": r.get("total_money")})

# ---------- 全局异常 ----------
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    payload = {"error": str(err), "type": err.__class__.__name__}
    if isinstance(err, requests.HTTPError):
        resp = err.response
        if resp is not None:
            try:
                upstream = resp.json()
                if "errors" in upstream:
                    return jsonify(upstream), resp.status_code
                payload["response"] = upstream
            except Exception:
                payload["response_text"] = resp.text[:500]
            payload["status_code"] = resp.status_code
    return jsonify(payload), 500

# ---------- 启动 ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
