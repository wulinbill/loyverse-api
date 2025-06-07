# main.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os, requests

app = Flask(__name__)
CORS(app)

LOYVERSE_TOKEN = os.getenv("LOYVERSE_TOKEN")
BASE = "https://api.loyverse.com/v1.0"
HEAD = {"Authorization": f"Bearer {LOYVERSE_TOKEN}"}

# 关键 alias 规则（可继续扩展）
CRIT = {
    "Pepper Steak": ["pepper steak", "paper space", "peper estic", "peper steak", "bistec pepper", "carne pepper"],
    "Pollo Pepper": ["pollo pepper", "pollo pimiento", "peper pollo"]
}

def build_alias(item):
    extra = CRIT.get(item["name"], [])
    desc = item.get("description", "")
    if desc.lower().startswith("alias:"):
        extra += [a.strip() for a in desc[6:].split(",")]
    return extra

@app.get("/")
def health():
    return "OK", 200

@app.get("/version")
def version():
    return jsonify({"version": "1.1.1", "status": "online"})

@app.post("/get_menu")
def get_menu():
    items = []
    url = f"{BASE}/items"
    while url:
        r = requests.get(url, headers=HEAD).json()
        for it in r.get("items", []):
            items.append({
                "sku": it["sku"],
                "name": it["name"],
                "category": it["category_name"],
                "price_base": float(it["default_price"]),
                "aliases": build_alias(it)
            })
        url = r.get("cursor")
    return jsonify(items)

@app.post("/get_customer")
def get_cust():
    data = request.json or {}
    phone = (data.get("phone") or request.headers.get("X-Vapi-Caller") or "").strip()
    if not phone:
        return jsonify({"error": "Missing phone number"}), 400
    r = requests.get(f"{BASE}/customers?phone={phone}", headers=HEAD).json()
    if not r.get("customers"):
        return jsonify({})
    c = r["customers"][0]
    return jsonify({"customer_id": c["id"], "name": c["name"]})

@app.post("/create_customer")
def create_cust():
    data = request.json or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    if not name or not phone:
        return jsonify({"error": "Missing name or phone"}), 400
    r = requests.post(f"{BASE}/customers", headers=HEAD,
                      json={"name": name, "phone_number": phone}).json()
    if "id" not in r:
        return jsonify({"error": r}), 500
    return jsonify({"customer_id": r["id"]})

@app.post("/place_order")
def place_order():
    data = request.json or {}
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "Missing items"}), 400

    payload = {
        "line_items": [{"sku": i["sku"], "quantity": i.get("qty", 1)} for i in items],
        "payments": []
    }
    if customer_id and customer_id != "null":
        payload["customer_id"] = customer_id

    r = requests.post(f"{BASE}/receipts", headers=HEAD, json=payload).json()
    if "total_amount" not in r:
        return jsonify({"error": r}), 500
    return jsonify({"total_with_tax": r["total_amount"], "receipt_id": r["receipt_id"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
