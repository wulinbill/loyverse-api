import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------------------------------------------
# 配置 & 全局缓存
# -----------------------------------------------------------------------------
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")
STORE_ID = os.getenv("STORE_ID")
PORT = int(os.getenv("PORT", 5000))

if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, STORE_ID]):
    raise RuntimeError("请在 .env 中配置 CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, STORE_ID")

app = Flask(__name__)
CORS(app)

_TOKEN_CACHE = {}

# -----------------------------------------------------------------------------
# OAuth2 Token 获取／缓存
# -----------------------------------------------------------------------------
def get_token():
    if "access_token" in _TOKEN_CACHE:
        return _TOKEN_CACHE["access_token"]
    try:
        resp = requests.post(
            "https://api.loyverse.com/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": REFRESH_TOKEN,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        _TOKEN_CACHE["access_token"] = data["access_token"]
        return data["access_token"]
    except Exception as e:
        app.logger.error("Token 刷新失败：%s", e)
        raise

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json",
    }

# -----------------------------------------------------------------------------
# 路由
# -----------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        return "Loyverse API bridge running."
    
    # Handle POST webhook events
    event = request.json or {}
    
    # Log the incoming event for debugging
    app.logger.info("Received webhook event: %s", event)
    
    # Extract event details
    role = event.get("role")
    message = event.get("message", "")
    tool_calls = event.get("toolCalls", [])
    
    try:
        # Handle system initialization
        if role == "system":
            # Initialize menu and customer lookup
            menu_response = requests.get(
                "https://api.loyverse.com/v1.0/items",
                headers=loyverse_headers(),
                params={"limit": 250},
                timeout=5
            )
            menu_response.raise_for_status()
            return jsonify({"status": "initialized"}), 200
            
        # Handle tool calls
        if role == "tool_calls":
            for tool_call in tool_calls:
                function_name = tool_call.get("function", {}).get("name")
                arguments = json.loads(tool_call.get("function", {}).get("arguments", "{}"))
                
                if function_name == "create_customer":
                    # Extract phone from caller_id or use provided phone
                    phone = arguments.get("phone", "")
                    if phone == "caller_id":
                        phone = event.get("caller_id", "")
                    
                    resp = requests.post(
                        "https://api.loyverse.com/v1.0/customers",
                        headers=loyverse_headers(),
                        json={"name": arguments.get("name"), "phone_number": phone},
                        timeout=5
                    )
                    resp.raise_for_status()
                    return jsonify(resp.json()), 200
                    
                elif function_name == "get_menu":
                    resp = requests.get(
                        "https://api.loyverse.com/v1.0/items",
                        headers=loyverse_headers(),
                        params={"limit": 250},
                        timeout=5
                    )
                    resp.raise_for_status()
                    return jsonify(resp.json()), 200
                    
                elif function_name == "place_order":
                    resp = requests.post(
                        "https://api.loyverse.com/v1.0/receipts",
                        headers=loyverse_headers(),
                        json={
                            "store_id": STORE_ID,
                            "customer_id": arguments.get("customer_id"),
                            "line_items": arguments.get("items", [])
                        },
                        timeout=5
                    )
                    resp.raise_for_status()
                    return jsonify(resp.json()), 200
        
        # For all other events, just acknowledge receipt
        return jsonify({"status": "received"}), 200
        
    except Exception as e:
        app.logger.error("Error processing webhook event: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/get_menu", methods=["GET", "POST"])
def get_menu():
    """
    拉取所有 items，返回 [{sku, name, category_id, price, aliases}, ...]
    支持分页：limit=250、cursor
    """
    try:
        items = []
        cursor = None
        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            resp = requests.get(
                "https://api.loyverse.com/v1.0/items",
                headers=loyverse_headers(),
                params=params,
                timeout=5
            )
            resp.raise_for_status()
            body = resp.json()
            batch = body.get("items", [])
            for it in batch:
                items.append({
                    "sku": it["variants"][0]["variant_id"],  # 默认取第一个变体
                    "name": it["item_name"],
                    "category_id": it["category_id"],
                    "price": it["variants"][0]["stores"][0]["price"],
                    "aliases": it.get("aliases", [])
                })
            cursor = body.get("cursor")
            if not cursor:
                break
        return jsonify({"items": items})
    except Exception as e:
        app.logger.error("get_menu 出错：%s", e)
        return jsonify({"error": "failed_to_fetch_menu", "details": str(e)}), 500

@app.route("/get_customer", methods=["POST"])
def get_customer():
    data = request.json or {}
    phone = data.get("phone", "")
    try:
        resp = requests.get(
            "https://api.loyverse.com/v1.0/customers",
            headers=loyverse_headers(),
            params={"phone_number": phone},
            timeout=5
        )
        resp.raise_for_status()
        custs = resp.json().get("customers", [])
        if custs:
            c = custs[0]
            return jsonify({"customer_id": c["id"], "name": c["name"]})
        return jsonify({"customer_id": None, "name": None})
    except Exception as e:
        app.logger.error("get_customer 出错：%s", e)
        return jsonify({"error": "failed_to_get_customer", "details": str(e)}), 500

@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.json or {}
    name = data.get("name")
    phone = data.get("phone")
    if not name or not phone:
        return jsonify({"error": "missing_parameters"}), 400
    try:
        resp = requests.post(
            "https://api.loyverse.com/v1.0/customers",
            headers=loyverse_headers(),
            json={"name": name, "phone_number": phone},
            timeout=5
        )
        resp.raise_for_status()
        body = resp.json()
        return jsonify({"customer_id": body["id"]})
    except Exception as e:
        app.logger.error("create_customer 出错：%s", e)
        return jsonify({"error": "failed_to_create_customer", "details": str(e)}), 500

@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json or {}
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    if customer_id is None or not isinstance(items, list) or not items:
        return jsonify({"error": "missing_parameters"}), 400

    # 构造 line_items
    line_items = []
    for it in items:
        sku = it.get("sku")
        qty = it.get("qty", 1)
        if not sku or qty <= 0:
            continue
        line_items.append({
            "item_variation_id": sku,
            "quantity": qty
        })
    if not line_items:
        return jsonify({"error": "no_valid_items"}), 400

    payload = {
        "store_id": STORE_ID,
        "customer_id": customer_id,
        "line_items": line_items
    }
    try:
        resp = requests.post(
            "https://api.loyverse.com/v1.0/receipts",
            headers=loyverse_headers(),
            json=payload,
            timeout=5
        )
        resp.raise_for_status()
        return jsonify(resp.json())
    except Exception as e:
        app.logger.error("place_order 出错：%s", e)
        return jsonify({"error": "failed_to_place_order", "details": str(e)}), 500

# -----------------------------------------------------------------------------
# 启动
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
