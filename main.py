
from flask import Flask, request, jsonify
import os, requests

app = Flask(__name__)

LOYVERSE_TOKEN = os.getenv("LOYVERSE_TOKEN")
BASE = "https://api.loyverse.com/v1.0"
HEAD = {"Authorization": f"Bearer {LOYVERSE_TOKEN}"}

@app.get("/")
def health():
    return "OK", 200

CRIT = {
    "Pepper Steak": ["pepper steak","paper space","peper estic","peper steak","bistec pepper","carne pepper"],
    "Pollo Pepper": ["pollo pepper","pollo pimiento","peper pollo"]
}

def build_alias(item):
    extra = CRIT.get(item["name"], [])
    desc  = item.get("description","")
    if desc.lower().startswith("alias:"):
        extra += [a.strip() for a in desc[6:].split(",")]
    return extra

@app.post("/get_menu")
def get_menu():
    items = []
    url   = f"{BASE}/items"
    while url:
        r = requests.get(url, headers=HEAD).json()
        for it in r["items"]:
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
    phone = request.json["phone"]
    r = requests.get(f"{BASE}/customers?phone={phone}", headers=HEAD).json()
    if not r["customers"]:
        return jsonify({})
    c = r["customers"][0]
    return jsonify({"customer_id": c["id"], "name": c["name"]})

@app.post("/create_customer")
def create_cust():
    data = request.json
    r = requests.post(f"{BASE}/customers", headers=HEAD,
                      json={"name": data["name"], "phone_number": data["phone"]}).json()
    return jsonify({"customer_id": r["id"]})

@app.post("/place_order")
def place_order():
    data = request.json
    lines = [{"sku": i["sku"], "quantity": i.get("qty",1)} for i in data["items"]]
    r = requests.post(f"{BASE}/receipts", headers=HEAD,
                      json={"customer_id": data["customer_id"], "line_items": lines, "payments":[]}).json()
    return jsonify({"total_with_tax": r["total_amount"], "receipt_id": r["receipt_id"]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
))
