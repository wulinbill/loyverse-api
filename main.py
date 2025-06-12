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
_MENU_CACHE = {}
_CUSTOMER_CACHE = {}

# 挂起队列（简单内存，实现 demo 功能）
_PENDING_CUSTOMER_CREATIONS = []  # [{"name": str|None, "phone": str|None}]
_PENDING_ORDERS = []              # [{"customer_id": str|None, "items": list}]

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
            try:
                # Initialize menu
                menu_response = requests.get(
                    "https://api.loyverse.com/v1.0/items",
                    headers=loyverse_headers(),
                    params={"limit": 250},
                    timeout=5
                )
                menu_response.raise_for_status()
                menu_data = menu_response.json()
                
                # Process and cache menu items
                for item in menu_data.get("items", []):
                    item_id = item.get("id")
                    if item_id:
                        variants = item.get("variants", [{}])[0]
                        stores = variants.get("stores", [{}])[0]
                        _MENU_CACHE[item_id] = {
                            "sku": variants.get("variant_id"),
                            "name": item.get("item_name"),
                            "category": item.get("category_id"),
                            "price": stores.get("price"),
                            "aliases": item.get("aliases", [])
                        }
                
                app.logger.info("Menu initialized with %d items", len(_MENU_CACHE))
                return jsonify({"status": "initialized", "menu_items": len(_MENU_CACHE)}), 200
            except Exception as e:
                app.logger.error("Failed to initialize menu: %s", e)
                return jsonify({"error": "menu_initialization_failed", "details": str(e)}), 500
            
        # Handle tool calls
        if role == "tool_calls":
            results = []
            for tool_call in tool_calls:
                try:
                    function_name = tool_call.get("function", {}).get("name")
                    arguments = json.loads(tool_call.get("function", {}).get("arguments", "{}"))
                    tool_call_id = tool_call.get("id")
                    
                    if not function_name or not tool_call_id:
                        continue
                    
                    result = None
                    if function_name == "create_customer":
                        # Extract phone from caller_id or use provided phone
                        phone = arguments.get("phone", "")
                        if phone == "caller_id":
                            phone = event.get("caller_id", "")
                            if not phone:
                                result = {"error": "missing_caller_id"}
                                continue
                        
                        name = arguments.get("name")
                        if not name:
                            result = {"error": "missing_name"}
                            continue
                        
                        # Check if customer already exists
                        existing_customer = requests.get(
                            "https://api.loyverse.com/v1.0/customers",
                            headers=loyverse_headers(),
                            params={"phone_number": phone},
                            timeout=5
                        )
                        existing_customer.raise_for_status()
                        existing_data = existing_customer.json()
                        
                        if existing_data.get("customers"):
                            customer = existing_data["customers"][0]
                            _CUSTOMER_CACHE[phone] = {
                                "id": customer["id"],
                                "name": customer["name"]
                            }
                            app.logger.info("Found existing customer: %s", customer["id"])
                            result = _CUSTOMER_CACHE[phone]
                        else:
                            # Create new customer
                            resp = requests.post(
                                "https://api.loyverse.com/v1.0/customers",
                                headers=loyverse_headers(),
                                json={"name": name, "phone_number": phone},
                                timeout=5
                            )
                            resp.raise_for_status()
                            customer_data = resp.json()
                            _CUSTOMER_CACHE[phone] = {
                                "id": customer_data["id"],
                                "name": name
                            }
                            app.logger.info("Created new customer: %s", customer_data["id"])
                            result = _CUSTOMER_CACHE[phone]
                            
                    elif function_name == "get_menu":
                        if not _MENU_CACHE:
                            # Refresh menu if cache is empty
                            menu_response = requests.get(
                                "https://api.loyverse.com/v1.0/items",
                                headers=loyverse_headers(),
                                params={"limit": 250},
                                timeout=5
                            )
                            menu_response.raise_for_status()
                            menu_data = menu_response.json()
                            
                            for item in menu_data.get("items", []):
                                item_id = item.get("id")
                                if item_id:
                                    variants = item.get("variants", [{}])[0]
                                    stores = variants.get("stores", [{}])[0]
                                    _MENU_CACHE[item_id] = {
                                        "sku": variants.get("variant_id"),
                                        "name": item.get("item_name"),
                                        "category": item.get("category_id"),
                                        "price": stores.get("price"),
                                        "aliases": item.get("aliases", [])
                                    }
                        
                        app.logger.info("Returning menu with %d items", len(_MENU_CACHE))
                        result = {"items": list(_MENU_CACHE.values())}
                        
                    elif function_name == "place_order":
                        customer_id = arguments.get("customer_id")
                        items = arguments.get("items", [])
                        
                        if not items:
                            result = {"error": "no_items_provided"}
                            continue
                            
                        # Validate items against menu
                        valid_items = []
                        for item in items:
                            sku = item.get("sku")
                            qty = item.get("qty", 1)
                            if not sku or qty <= 0:
                                continue
                                
                            # Find item in menu cache
                            menu_item = next((m for m in _MENU_CACHE.values() if m["sku"] == sku), None)
                            if menu_item:
                                valid_items.append({
                                    "item_variation_id": sku,
                                    "quantity": qty
                                })
                        
                        if not valid_items:
                            result = {"error": "no_valid_items"}
                            continue
                            
                        resp = requests.post(
                            "https://api.loyverse.com/v1.0/receipts",
                            headers=loyverse_headers(),
                            json={
                                "store_id": STORE_ID,
                                "customer_id": customer_id,
                                "line_items": valid_items
                            },
                            timeout=5
                        )
                        resp.raise_for_status()
                        order_data = resp.json()
                        
                        # Calculate preparation time based on main dishes
                        main_dishes = [item for item in valid_items if not any(
                            cat in _MENU_CACHE.get(item["item_variation_id"], {}).get("category", "").lower()
                            for cat in ["acompanantes", "aparte", "extra", "salsa", "no", "poco"]
                        )]
                        prep_time = "15" if len(main_dishes) >= 3 else "10"
                        
                        app.logger.info("Order placed successfully: %s", order_data.get("id"))
                        result = {
                            "order": order_data,
                            "preparation_time": prep_time
                        }
                    
                    if result is not None:
                        results.append({
                            "name": function_name,
                            "role": "tool_call_result",
                            "toolCallId": tool_call_id,
                            "result": result
                        })
                        
                except Exception as e:
                    app.logger.error("Error processing tool call %s: %s", function_name, e)
                    results.append({
                        "name": function_name,
                        "role": "tool_call_result",
                        "toolCallId": tool_call.get("id"),
                        "result": {"error": str(e)}
                    })
            
            return jsonify(results), 200
        
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
    app.logger.debug("/create_customer payload: %s", data)

    # 若信息不完整，先放队列，返回 accepted 让对话继续
    if not name or not phone or phone in ("caller_id", "", None, "null", "NULL"):
        _PENDING_CUSTOMER_CREATIONS.append({"name": name, "phone": phone})
        return jsonify({"customer_id": None, "status": "accepted"}), 200

    # 正常流程：先检查本地缓存或远程是否已存在
    try:
        # 查缓存
        if phone in _CUSTOMER_CACHE:
            return jsonify({"customer_id": _CUSTOMER_CACHE[phone]["id"], "status": "cached"})

        # 远程查重
        resp_chk = requests.get(
            "https://api.loyverse.com/v1.0/customers",
            headers=loyverse_headers(),
            params={"phone_number": phone},
            timeout=5
        )
        resp_chk.raise_for_status()
        customers = resp_chk.json().get("customers", [])
        if customers:
            cid = customers[0]["id"]
            _CUSTOMER_CACHE[phone] = {"id": cid, "name": customers[0]["name"]}
            return jsonify({"customer_id": cid, "status": "existing"})

        # 创建新客户
        resp = requests.post(
            "https://api.loyverse.com/v1.0/customers",
            headers=loyverse_headers(),
            json={"name": name, "phone_number": phone},
            timeout=5
        )
        resp.raise_for_status()
        body = resp.json()
        cid = body.get("id")
        _CUSTOMER_CACHE[phone] = {"id": cid, "name": name}
        return jsonify({"customer_id": cid, "status": "created"})
    except Exception as e:
        app.logger.error("create_customer error: %s", e)
        # 出错也不要阻断对话，放入挂起队列
        _PENDING_CUSTOMER_CREATIONS.append({"name": name, "phone": phone})
        return jsonify({"customer_id": None, "status": "queued", "details": str(e)}), 200

@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json or {}
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    app.logger.debug("/place_order payload: %s", data)

    if not isinstance(items, list) or not items:
        _PENDING_ORDERS.append({"customer_id": customer_id, "items": items})
        return jsonify({"status": "queued", "reason": "missing_items"}), 200

    # 验证并转换 line_items
    line_items = []
    for it in items:
        sku = it.get("sku")
        qty = it.get("qty", 1)
        if not sku or qty <= 0:
            continue
        # 若能在缓存里找到匹配 SKU，则为有效项目
        if any(m.get("sku") == sku for m in _MENU_CACHE.values()):
            line_items.append({"item_variation_id": sku, "quantity": qty})
    
    if not line_items:
        # 全部是 alias 或未知 SKU，放队列等待人工/后台映射
        _PENDING_ORDERS.append({"customer_id": customer_id, "items": items})
        return jsonify({"status": "queued_for_mapping"}), 200

    payload = {"store_id": STORE_ID, "line_items": line_items}
    if customer_id not in (None, "", "null", "NULL", "caller_id"):
        payload["customer_id"] = customer_id

    try:
        resp = requests.post(
            "https://api.loyverse.com/v1.0/receipts",
            headers=loyverse_headers(),
            json=payload,
            timeout=5
        )
        resp.raise_for_status()
        return jsonify({"status": "success", **resp.json()})
    except Exception as e:
        app.logger.error("place_order error: %s", e)
        _PENDING_ORDERS.append({"customer_id": customer_id, "items": items})
        return jsonify({"status": "queued", "details": str(e)}), 200

# -----------------------------------------------------------------------------
# 启动
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
