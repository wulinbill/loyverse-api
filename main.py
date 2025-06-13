import os
import time
import logging
import traceback
import requests
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

# === 配置项（Environment Variables） ===
CLIENT_ID            = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET        = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN        = os.getenv("LOYVERSE_REFRESH_TOKEN")
REDIRECT_URI         = os.getenv("LOYVERSE_REDIRECT_URI")
STORE_ID             = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

# === 常量 ===
OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"
API_BASE        = "https://api.loyverse.com/v1.0"

app = Flask(__name__)
CORS(app)
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

def extract_phone():
    """
    从 VAPI 回调 JSON 中提取来电号码：
    - 优先取 "caller_id"
    - 否则取 "from":{"id":...}
    """
    data = request.json or {}
    phone = data.get("caller_id")
    if not phone and isinstance(data.get("from"), dict):
        phone = data["from"].get("id")
    return phone

def ensure_customer_by_phone(phone):
    """
    判断客户是否存在（按 phone_number），不存在则创建：
    返回 (customer_id, name)
    """
    if not phone:
        return None, None

    # 1. 查询所有客户（limit 250，可根据需要翻页）
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"limit": 250},
        timeout=15
    )
    resp.raise_for_status()
    customers = resp.json().get("customers", [])
    # 2. 客户端过滤
    for c in customers:
        if c.get("phone_number") == phone:
            return c["id"], c.get("name")

    # 3. 如果没找到，则创建
    payload = {"name": phone, "phone_number": phone}
    resp2 = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json=payload,
        timeout=15
    )
    resp2.raise_for_status()
    new_id = resp2.json().get("id")
    return new_id, None

# ---------- 首页 & OAuth 回调（保持不变） ----------
@app.route("/")
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
        "<p>点击下方链接完成授权：</p>"
        f"<p><a href='{auth_url}'>🔗 Connect Loyverse</a></p>"
    )

def handle_callback():
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
        timeout=15
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

# ---------- 获取菜单（不变） ----------
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
            variants=[]
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if str(s["store_id"])==str(STORE_ID) and s.get("available_for_sale"):
                        variants.append({"variant_id":v["variant_id"],"price":s["price"]})
            if variants:
                items.append({
                    "sku":variants[0]["variant_id"],
                    "name":it["item_name"],
                    "category":it.get("category_id"),
                    "price_base":variants[0]["price"],
                    "aliases":[],
                })
        cursor = data.get("cursor")
        if not cursor:
            break
    return jsonify({"menu": items})

# ---------- 下单（自动获取/创建客户） ----------
@app.route("/place_order", methods=["POST"])
def place_order():
    # 1. 提取来电号码并确保客户存在
    phone, req = extract_phone(), request.json or {}
    customer_id, _ = ensure_customer_by_phone(phone)

    # 2. 构建 line_items
    items = req.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400
    line_items = [{"variant_id":it["variant_id"],"quantity":it["quantity"]} for it in items]

    # 3. 预估总价
    resp_est = requests.post(
        f"{API_BASE}/receipts/preview",
        headers=loyverse_headers(),
        json={"store_id": STORE_ID, "line_items": line_items},
        timeout=15
    )
    resp_est.raise_for_status()
    total_money = resp_est.json().get("total_money")

    # 4. 下单
    body = {
        "customer_id":   customer_id,
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items":    line_items,
        "payments": [
            {"payment_type_id": CASH_PAYMENT_TYPE_ID, "money_amount": total_money}
        ]
    }
    resp = requests.post(
        f"{API_BASE}/receipts",
        headers=loyverse_headers(),
        json=body,
        timeout=15
    )
    resp.raise_for_status()
    r = resp.json()
    return jsonify({"receipt_number":r.get("receipt_number"),"total_money":r.get("total_money")})

# ---------- 全局异常处理（不变） ----------
@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception: %s", err)
    traceback.print_exc()
    payload = {"error": str(err), "type": err.__class__.__name__}
    if isinstance(err, requests.HTTPError):
        resp = err.response
        if resp:
            try:
                up = resp.json()
                if "errors" in up:
                    return jsonify(up), resp.status_code
                payload["response"] = up
            except:
                payload["response_text"] = resp.text[:500]
            payload["status_code"] = resp.status_code
    return jsonify(payload), 500

# ---------- 启动 ----------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
