import os
import time
import json
import logging
import traceback
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# === Environment Variables ===
CLIENT_ID            = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET        = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN        = os.getenv("LOYVERSE_REFRESH_TOKEN")
STORE_ID             = os.getenv("LOYVERSE_STORE_ID")
CASH_PAYMENT_TYPE_ID = os.getenv("LOYVERSE_CASH_PAYMENT_TYPE_ID")

# === Endpoints ===
API_BASE        = "https://api.loyverse.com/v1.0"
OAUTH_TOKEN_URL = "https://api.loyverse.com/oauth/token"

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# In-memory token cache
token_cache = {"access_token": None, "expires_at": 0}


def _refresh_access_token():
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("Missing CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN")
    resp = requests.post(
        OAUTH_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    token_cache["access_token"] = data["access_token"]
    token_cache["expires_at"]   = time.time() + data.get("expires_in", 0) - 60


def get_access_token():
    if token_cache["access_token"] is None or time.time() >= token_cache["expires_at"]:
        _refresh_access_token()
    return token_cache["access_token"]


def loyverse_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def extract_phone(body: dict) -> str:
    # 从 VAPI webhook 或请求体中提取来电号码
    phone = None
    # VAPI 回调结构
    if "call" in body and "customer" in body["call"]:
        phone = body["call"]["customer"].get("number")
    # 普通 POST 时在 body 中
    if not phone:
        phone = body.get("phone")
    return phone or ""


def ensure_customer_by_phone(phone: str) -> str:
    resp = requests.get(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        params={"phone_number": phone, "limit": 1}, timeout=15
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        return custs[0]["id"]
    # 不存在则创建
    resp2 = requests.post(
        f"{API_BASE}/customers",
        headers=loyverse_headers(),
        json={"name": phone, "phone_number": phone}, timeout=15
    )
    resp2.raise_for_status()
    return resp2.json().get("id")


@app.route("/", methods=["GET", "POST", "OPTIONS"])
def root():
    if request.method == "OPTIONS":
        return "", 200
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        msg = body.get("message", {})
        # 处理 VAPI 工具调用
        if msg.get("type") == "tool-calls":
            results = []
            for call in msg.get("toolCalls", []):
                cid = call.get("id")
                fname = call["function"]["name"]
                args = json.loads(call["function"]["arguments"])
                try:
                    # 替换 caller_id
                    if args.get("phone") == "caller_id":
                        args["phone"] = extract_phone(body)
                    # 分派
                    if fname == "get_menu":
                        res = get_menu_internal()
                    elif fname == "get_customer":
                        res = get_customer_internal(args)
                    elif fname == "create_customer":
                        res = create_customer_internal(args)
                    elif fname == "place_order":
                        res = place_order_internal(args)
                    else:
                        res = {"error": f"Unknown function {fname}"}
                except Exception as e:
                    logging.error("Error in tool %s: %s", fname, e)
                    res = {"error": str(e)}
                results.append({"id": cid, "result": res})
            return jsonify({"toolCallResults": results})
    # GET 返回状态
    return jsonify({"message": "Loyverse OAuth App is running."})


def get_menu_internal():
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
            for v in it.get("variants", []):
                for s in v.get("stores", []):
                    if str(s.get("store_id")) == str(STORE_ID) and s.get("available_for_sale"):
                        items.append({
                            "variant_id": v["variant_id"],
                            "item_name":  it["item_name"],
                            "category_id": it.get("category_id"),
                            "price_base": s.get("price"),
                        })
        cursor = data.get("cursor")
        if not cursor:
            break
    return {"menu": items}


def get_customer_internal(args):
    phone = args.get("phone")
    cid = ensure_customer_by_phone(phone)
    return {"customer_id": cid}


def create_customer_internal(args):
    name = args.get("name")
    phone = args.get("phone")
    if not name or not phone:
        raise RuntimeError("name & phone are required")
    resp = requests.post(
        f"{API_BASE}/customers", headers=loyverse_headers(),
        json={"name": name, "phone_number": phone}, timeout=15
    )
    resp.raise_for_status()
    return {"customer_id": resp.json().get("id")}


def place_order_internal(args):
    items = args.get("items", [])
    if not items:
        raise RuntimeError("items array is required")
    # 调用内部 get_menu 获取价格
    menu = get_menu_internal()["menu"]
    price_map = {it["variant_id"]: it["price_base"] for it in menu}
    line_items = []
    total = 0
    for o in items:
        vid = o.get("sku") or o.get("variant_id")
        qty = int(o.get("qty") or o.get("quantity") or 0)
        price = price_map.get(vid)
        if price is None:
            raise RuntimeError(f"unknown variant_id {vid}")
        line_items.append({"variant_id": vid, "quantity": qty})
        total += price * qty
    # 客户
    cust = args.get("customer_id")
    if not cust:
        cust = ensure_customer_by_phone(args.get("phone", ""))
    payload = {
        "store_id":      STORE_ID,
        "dining_option": "TAKEAWAY",
        "customer_id":   cust,
        "line_items":    line_items,
        "payments":      [{"payment_type_id": CASH_PAYMENT_TYPE_ID, "money_amount": total}]
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    r = resp.json()
    return {"receipt_number": r.get("receipt_number"), "total_money": r.get("total_money")}


@app.errorhandler(Exception)
def handle_exception(err):
    logging.error("Unhandled exception", exc_info=err)
    resp = getattr(err, 'response', None)
    payload = {"error": str(err)}
    if resp is not None:
        try:
            payload["detail"] = resp.json()
        except:
            payload["detail_text"] = resp.text
        return jsonify(payload), resp.status_code
    return jsonify(payload), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
