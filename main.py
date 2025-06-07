from flask import Flask, request, jsonify
from flask_cors import CORS
import os, requests

app = Flask(__name__)
CORS(app)

LOYVERSE_TOKEN = os.getenv("LOYVERSE_TOKEN")
BASE = "https://api.loyverse.com/v1.0"
HEAD = {"Authorization": f"Bearer {LOYVERSE_TOKEN}"}

# 关键别名配置
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

@app.post("/get_menu")
def get_menu():
    items = []
    url = f"{BASE}/items"
    while url:
        r = requests.get(url, headers=HEAD).json()
        for it in r.get("items", []):
            if "sku" not in it:
                continue
            items.append({
                "sku": it["sku"],
                "name": it["name"],
                "category": it.get("category_name", ""),
                "price_base": float(it.get("default_price", 0)),
                "aliases": build_alias(it)
            })
        url = r.get("cursor")
    return jsonify(items)

@app.post("/get_customer")
def get_cust():
    data = request.json or {}
    phone = data.get("phone") or request.headers.get("X-Vapi-Caller")
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
    phone = data.get("phone") or request.headers.get("X-Vapi-Caller")
    name = data.get("name")
    if not name or not phone:
        return jsonify({"error": "Missing name or phone"}), 400
    r = requests.post(f"{BASE}/customers", headers=HEAD,
                      json={"name": name, "phone_number": phone}).json()
    if "id" not in r:
        return jsonify({"error": r}), 500
    return jsonify({"customer_id": r["id"]})

@app.post("/place_order")
def place_order():
    data = request.json
    customer_id = data.get("customer_id")
    items = data.get("items", [])
    if not customer_id or not items:
        return jsonify({"error": "Missing customer_id or items"}), 400
    lines = [{"sku": i["sku"], "quantity": i.get("qty", 1)} for i in items]
    r = requests.post(f"{BASE}/receipts", headers=HEAD,
                      json={"customer_id": customer_id, "line_items": lines, "payments": []}).json()
    if "total_amount" not in r:
        return jsonify({"error": r}), 500
    return jsonify({"total_with_tax": r["total_amount"], "receipt_id": r["receipt_id"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
