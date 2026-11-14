"""Laptop B: Board B -> SQLite -> dashboard. Run: python base/app.py"""
import os
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

from db import DB_PATH, init as init_db, list_messages, set_status
from ingest import ingest
from radio import Radio

load_dotenv()

app = Flask(__name__)
init_db()

PORT = os.getenv("RADIO_PORT", "")
radio = None

if PORT:
    try:
        radio = Radio(PORT)
        radio.setup(receiver=True)
        threading.Thread(target=radio.listen, args=(lambda payload, rssi, snr=None: ingest(payload, rssi, snr),), daemon=True).start()
        print("Listening on", PORT)
    except Exception as exc:
        print(f"Radio setup failed: {exc}")
        radio = None


@app.get("/")
def home():
    return render_template("dashboard.html")


@app.get("/api/messages")
def messages():
    since = request.args.get("since")
    return jsonify(list_messages(since=since))


@app.post("/api/messages/<mid>/status")
def api_set_status(mid):
    data = request.get_json(force=True, silent=True) or {}
    status = data.get("status") or request.form.get("status")
    if status not in {"ack", "resolved"}:
        return jsonify({"ok": False, "error": "status must be 'ack' or 'resolved'"}), 400
    set_status(mid, status)
    return jsonify({"ok": True})


@app.get("/api/health")
def health():
    now = datetime.now(timezone.utc).isoformat()
    msg_count = 0
    rows = list_messages()
    if rows:
        msg_count = len(rows)
    return jsonify({"ok": True, "messages": msg_count, "radio": getattr(radio, "health", lambda: {"ok": False})() if radio else {"ok": False, "mode": "dry"}, "timestamp": now})


@app.post("/api/simulate")
def simulate():
    ingest("A99|DUB-3-F14|MED|needs fridge for insulin", rssi=-55, snr=12)
    return jsonify({"ok": True})


@app.post("/api/clear")
def clear():
    conn = __import__("sqlite3").connect(DB_PATH)
    conn.execute("DELETE FROM messages")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
