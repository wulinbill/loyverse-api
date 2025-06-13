import os
import time
import logging
import traceback
import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS  # ← 全局跨域

# === 配置项（Env Variables） ===
CLIENT_ID            = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET        = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN        = os.getenv("LOYVERSE_REFRESH_TOKEN")
REDIRECT_URI         = os.getenv("LOYVERSE_REDIRECT_URI")
STORE_ID             = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"
API_BASE        = "https://api.loyverse.com/v1.0"

app = Flask(__name__)
CORS(app)  # ← 自动为所有路由添加 Access-Control-Allow-… 头

logging.basicConfig(level=logging.INFO)

# 简易内存缓存 Access Token
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

def extract_phone():
    data = request.get_json() or {}
    # 依次尝试可能的字段
    phone = data.get("caller_id")
    if not phone and isinstance(data.get("from"), dict):
        phone = data["from"].get("id")
    if not phone and isinstance(data.get("call"), dict):
        phone = data["call"].get("customer", {}).get("number")
    if not phone and isinstance(data.get("customer"), dict):
        phone = data["customer"].get("number")
    return phone

def ensure_customer_by_phone(phone):
    if not phone:
        return None, None
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"limit": 250},
        timeout=15,
    )
    resp.raise_for_status()
    for c in resp.json().get("customers", []):
        if c.get("phone_number") == phone:
            return c["id"], c.get("name")
    # 不存在则创建
    payload = {"name": phone, "phone_number": phone}
    resp2 = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json=payload,
        timeout=15,
    )
    resp2.raise_for_status()
    return resp2.json().get("id"), None

# ———— OAuth 首页 & 回调 ————
@app.route("/", methods=["GET"])
def index():
    if not all([CLIENT_ID, REDIRECT_URI]):
        return "<h3>请先配置 LOYVERSE_CLIENT_ID 与 LOYVERSE_REDIRECT_URI</h3>", 400
    scopes = ["stores.read","customers.read","customers.write","items.read","receipts.read","receipts.write"]
    auth_url = (
        "https://api.loyverse.com/oauth/authorize"
        f"?response_type=code&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={'%20'.join(scopes)}"
    )
    return (
        "<h2>Loyverse OAuth Demo</h2>"
        f"<p><a href='{auth_url}'>🔗 Connect Loyverse</a></p>"
    )

@app.route("/oauth/callback", methods=["GET"])
@app.route("/callback",       methods=["GET"])
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "缺少 ?code= 参数", 400
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": REDIRECT_URI,
            "client_id":    CLIENT_ID,
            "client_secret":CLIENT_SECRET,
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
        <p style="color:red;">⚠️ 请立即复制并妥善保存 Refresh Token。</p>
        """,
        access=tok["access_token"],
        refresh=tok["refresh_token"],
    )

# ———— 菜单接口 ————
@app.route("/get_menu", methods=["POST"])
def get_menu():
    items = []; cursor = None
    while True:
        params = {"limit":250}
        if cursor: params["cursor"] = cursor
        resp = requests.get(f"{API_BASE}/items", headers=loyverse_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for it in data.get("items", []):
            variants = [
                {"variant_id":v["variant_id"],"price":s["price"]}
                for v in it.get("variants",[])
                for s in v.get("stores",[])
                if str(s["store_id"])==str(STORE_ID) and s.get("available_for_sale")
            ]
            if variants:
                items.append({
                    "sku":variants[0]["variant_id"],
                    "name":it["item_name"],
                    "category":it.get("category_id"),
                    "price_base":variants[0]["price"],
                    "aliases":[],
                })
        cursor = data.get("cursor")
        if not cursor: break
    return jsonify({"menu": items})

# ———— 查客 & 创客 ————
@app.route("/get_customer", methods=["POST"])
def get_customer():
    body = request.get_json() or {}
    phone = body.get("phone") or extract_phone()
    if not phone:
        return jsonify({"error":"phone is required"}), 400
    cust_id,name = ensure_customer_by_phone(phone)
    return jsonify({"customer_id":cust_id,"name":name})

@app.route("/create_customer", methods=["POST"])
def create_customer():
    body = request.get_json() or {}
    name  = body.get("name")
    phone = body.get("phone") or extract_phone()
    if not name or not phone:
        return jsonify({"error":"name & phone are required"}), 400
    payload = {"name":name,"phone_number":phone}
    resp = requests.post(f"{API_BASE}/customers", headers=loyverse_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return jsonify({"customer_id":resp.json().get("id")})

# ———— 下单 ————
@app.route("/place_order", methods=["POST"])
def place_order():
    req = request.get_json() or {}
    items = req.get("items",[])
    if not items:
        return jsonify({"error":"items array is required"}),400

    # 自动取来电号码，确保 customer_id
    phone = extract_phone()
    customer_id,_ = ensure_customer_by_phone(phone)

    line_items = [{"variant_id":it["variant_id"],"quantity":it["quantity"]} for it in items]
    body = {
        "customer_id":   customer_id,
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items":    line_items,
        "payments":[
            {"payment_type_id":CASH_PAYMENT_TYPE_ID,"money_amount":None}
        ]
    }
    # 直接下单，拿 total_money
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return jsonify({
        "receipt_number": data.get("receipt_number"),
        "total_money":    data.get("total_money"),
    })

# ———— 全局异常 ————
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    return jsonify({"error":str(err)}), getattr(err.response,"status_code",500)

if __name__ == "__main__":
    port = int(os.getenv("PORT",5000))
    app.run(host="0.0.0.0",port=port)
