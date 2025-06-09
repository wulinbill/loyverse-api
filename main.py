import os
import logging
from functools import wraps

import requests
from flask import Flask, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ——— 日志配置 ———
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

TOKEN_CACHE = {}

def json_endpoint(fn):
    """捕获异常并转成 Vapi-friendly 的 dict."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except requests.RequestException as e:
            logger.exception("External request failed")
            return {"error": "external_request_failed", "details": str(e)}
        except Exception as e:
            logger.exception("Internal error")
            return {"error": "internal_error", "details": str(e)}
    return wrapper

def get_token():
    if "access_token" in TOKEN_CACHE:
        return TOKEN_CACHE["access_token"]

    url = "https://api.loyverse.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("REFRESH_TOKEN"),
        "client_id": os.getenv("CLIENT_ID"),
        "client_secret": os.getenv("CLIENT_SECRET"),
    }
    logger.info("Refreshing Loyverse token…")
    resp = requests.post(url, headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data, timeout=10)
    if resp.status_code != 200:
        raise Exception(f"Token refresh failed: {resp.status_code} {resp.text}")
    tok = resp.json()["access_token"]
    TOKEN_CACHE["access_token"] = tok
    return tok

def loyverse_headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type": "application/json"
    }


@app.route("/", methods=["GET"])
def health_check():
    return "OK"


@app.route("/get_menu", methods=["POST"])
@json_endpoint
def get_menu():
    """返回 items 列表，格式：
      {"items":[{"sku":..., "name":..., "category":..., "price":...}, ...]}
    """
    r = requests.get("https://api.loyverse.com/v1.0/items", headers=loyverse_headers(), timeout=10)
    r.raise_for_status()
    items = r.json().get("items", [])
    slim = []
    for it in items:
        variant = it["variants"][0]
        store_info = variant["stores"][0]
        slim.append({
            "sku": variant["variant_id"],
            "name": it["item_name"],
            "category": it.get("category_id"),
            "price": store_info["price"]
        })
    return {"items": slim}


@app.route("/get_customer", methods=["POST"])
@json_endpoint
def get_customer():
    phone = request.json.get("phone", "").strip()
    if not phone:
        return {"customer_id": None, "name": None}
    url = f"https://api.loyverse.com/v1.0/customers?phone_number={phone}"
    r = requests.get(url, headers=loyverse_headers(), timeout=10)
    r.raise_for_status()
    custs = r.json().get("customers", [])
    if not custs:
        return {"customer_id": None, "name": None}
    c = custs[0]
    return {"customer_id": c["id"], "name": c["name"]}


@app.route("/create_customer", methods=["POST"])
@json_endpoint
def create_customer():
    name = request.json.get("name")
    phone = request.json.get("phone")
    payload = {"name": name, "phone_number": phone}
    r = requests.post("https://api.loyverse.com/v1.0/customers",
                      headers=loyverse_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        raise Exception(f"Create customer failed: {r.status_code} {r.text}")
    cid = r.json()["id"]
    return {"customer_id": cid}


@app.route("/place_order", methods=["POST"])
@json_endpoint
def place_order():
    data = request.json
    items = data.get("items") or []
    customer_id = data.get("customer_id")
    if not items:
        raise Exception("No items provided")
    line_items = []
    for it in items:
        line_items.append({
            "item_variation_id": it["sku"],
            "quantity": it["qty"]
        })
    payload = {"line_items": line_items}
    if customer_id:
        payload["customer_id"] = customer_id

    r = requests.post("https://api.loyverse.com/v1.0/receipts",
                      headers=loyverse_headers(), json=payload, timeout=10)
    r.raise_for_status()
    rec = r.json()
    return {
        "receipt_id": rec.get("id"),
        "total_with_tax": rec.get("total_with_tax") or rec.get("total")
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
