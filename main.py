import os
import time
import logging
import traceback
from urllib.parse import urlencode

import requests
from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------
# Basic config & env vars
# ------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

CLIENT_ID              = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET          = os.getenv("LOYVERSE_CLIENT_SECRET")
REFRESH_TOKEN          = os.getenv("LOYVERSE_REFRESH_TOKEN")      # OAuth 流程获取
PERSONAL_TOKEN         = os.getenv("LOYVERSE_PERSONAL_TOKEN")     # 仅读权限，可选
STORE_ID               = os.getenv("LOYVERSE_STORE_ID")
SELF_URL               = os.getenv("https://loyverse-api.onrender.com")                     # 例如 https://loyverse-api.onrender.com
API_BASE               = "https://api.loyverse.com/v1.0"

# ------------------------------------------------------------
# Flask app
# ------------------------------------------------------------

app = Flask(__name__)
CORS(app)

TOKEN_CACHE = {"token": None, "expires_at": 0}  # 缓存 OAuth access_token

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _oauth_refresh():
    """Use refresh_token to obtain a new access_token and cache it."""
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise RuntimeError("Missing OAuth credentials; set CLIENT_ID/SECRET and REFRESH_TOKEN or use PERSONAL_TOKEN")

    resp = requests.post(
        f"{API_BASE}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    TOKEN_CACHE.update({
        "token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 0) - 60,
    })


def get_access_token() -> str:
    """Return a valid Bearer token: prefer personal token, fallback to OAuth refresh."""
    if PERSONAL_TOKEN:
        return PERSONAL_TOKEN.strip()

    if TOKEN_CACHE["token"] is None or time.time() >= TOKEN_CACHE["expires_at"]:
        _oauth_refresh()
    return TOKEN_CACHE["token"]


def loyverse_headers(content_type: str = "application/json"):
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": content_type,
    }

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route("/")
def index():
    return "Loyverse proxy online."

# ---------- OAuth helper endpoints (one-time use) ----------

@app.route("/auth_link")
def auth_link():
    """Generate an OAuth authorize URL with requested scopes."""
    scopes = request.args.get("scopes", "ITEMS_READ CUSTOMERS_READ CUSTOMERS_WRITE RECEIPTS_WRITE").replace("+", " ")
    params = urlencode({
        "client_id": CLIENT_ID,
        "scope": scopes,
        "response_type": "code",
        "redirect_uri": f"{SELF_URL}/callback",
    })
    return redirect(f"https://api.loyverse.com/oauth/authorize?{params}")


@app.route("/callback")
def oauth_callback():
    """Handle OAuth redirect, exchange code → refresh_token and print for manual copy."""
    code = request.args.get("code")
    if not code:
        return "Missing code parameter", 400

    token_resp = requests.post(
        f"{API_BASE}/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": f"{SELF_URL}/callback",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    data = token_resp.json()
    logging.info("<<< NEW REFRESH_TOKEN >>> %s", data.get("refresh_token"))
    return jsonify({"refresh_token": data.get("refresh_token"), "note": "Save this in LOYVERSE_REFRESH_TOKEN env var"})

# ---------- Business endpoints ----------

@app.route("/get_menu", methods=["POST"])
def get_menu():
    """Fetch all sellable items for this store, including variant price."""
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


@app.route("/get_customer", methods=["POST"])
def get_customer():
    phone = request.json.get("phone", "")
    resp = requests.get(
        f"{API_BASE}/customers", headers=loyverse_headers(), params={"phone_number": phone, "limit": 50}, timeout=15
    )
    resp.raise_for_status()
    custs = resp.json().get("customers", [])
    if custs:
        c = custs[0]
        return jsonify({"customer_id": c["id"], "name": c["name"]})
    return jsonify({"customer_id": None, "name": None})


@app.route("/create_customer", methods=["POST"])
def create_customer():
    data = request.json or {}
    if "name" not in data or "phone" not in data:
        return jsonify({"error": "name & phone are required"}), 400

    payload = {"name": data["name"], "phone_number": data["phone"]}
    resp = requests.post(f"{API_BASE}/customers", headers=loyverse_headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return jsonify({"customer_id": resp.json()["id"]})


@app.route("/place_order", methods=["POST"])
def place_order():
    data = request.json or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400

    body = {
        "customer_id": data.get("customer_id"),
        "store_id": STORE_ID,
        "dining_option": "TAKEAWAY",
        "line_items": [{"variant_id": it["variant_id"], "quantity": it["quantity"]} for it in items],
    }
    resp = requests.post(f"{API_BASE}/receipts", headers=loyverse_headers(), json=body, timeout=15)
    resp.raise_for_status()
    r = resp.json()
    return jsonify({"receipt_number": r.get("receipt_number"), "total_money": r.get("total_money")})

# ------------------------------------------------------------
# Global error handler
# ------------------------------------------------------------

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

# ------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
