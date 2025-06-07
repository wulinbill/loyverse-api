from flask import Flask, request, jsonify
import os, requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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
    customer_id = data.get("customer_id")

    # 检查是否需要新建顾客
    if not customer_id:
        phone = data["phone"]
        name = data["name"]
        r = requests.post(f"{BASE}/customers", headers=HEAD,
                          json={"name": name, "phone_number": phone}).json()
        customer_id = r["id"]

    lines = [{"sku": i["sku"], "quantity": i.get("qty",1)} for i in data["items"]]
    r = requests.post(f"{BASE}/receipts", headers=HEAD,
                      json={"customer_id": customer_id, "line_items": lines, "payments":[]}).json()
    return jsonify({"total_with_tax": r["total_amount"], "receipt_id": r["receipt_id"]})