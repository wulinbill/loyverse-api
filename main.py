
import os
import json
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

ACCESS_TOKEN = os.getenv("LOYVERSE_ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("LOYVERSE_REFRESH_TOKEN")
CLIENT_ID = os.getenv("LOYVERSE_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOYVERSE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("LOYVERSE_REDIRECT_URI")

LOYVERSE_API_BASE = "https://api.loyverse.com/v1.0"

def refresh_access_token():
    url = "https://api.loyverse.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        tokens = response.json()
        print("Access token refreshed.")
        return tokens["access_token"]
    else:
        print("Failed to refresh token.")
        return None

def get_headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

@app.route("/get_menu", methods=["POST"])
def get_menu():
    url = f"{LOYVERSE_API_BASE}/items"
    response = requests.get(url, headers=get_headers())
    if response.status_code == 401:
        new_token = refresh_access_token()
        if new_token:
            global ACCESS_TOKEN
            ACCESS_TOKEN = new_token
            response = requests.get(url, headers=get_headers())
    return jsonify(response.json()), response.status_code

@app.route("/")
def index():
    return "Loyverse API Integration Working"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
