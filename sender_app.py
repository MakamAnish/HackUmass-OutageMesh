"""Laptop A (help point): form -> LoRa; status page; listen for responder ACKs."""
import json
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

import outgoing
import radio_worker
from protocol import CATEGORIES, decode_ack, decode_done, encode, make_zone, next_id, sanitize
from ranging import estimate_range_m

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

BUILDING = os.getenv("BUILDING", "DUB")
FLOOR = os.getenv("FLOOR", "3")
NODE_ID = os.getenv("NODE_ID", "A")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

app = Flask(__name__)
log = logging.getLogger("sender")
outgoing.init()

CATS = {"MED": "Medical", "TRAP": "Trapped", "WATER": "Water", "SAFE": "Safe", "OTHER": "Other"}


def _on_packet(payload: str, rssi: int | None, snr: float | None) -> None:
    ack = decode_ack(payload)
    if ack is not None:
        status = ack.get("status", "HEARD")
        note = ack.get("note", ack.get("text", ""))
        if not outgoing.record_ack(ack["id"], status, note):
            return
        log.info("%s for %s note=%r rssi=%s", status, ack["id"], note, rssi)
        return
    done = decode_done(payload)
    if done is not None:
        if not outgoing.record_ack(done["id"], "DONE", done.get("text", "")):
            return
        log.info("DONE for %s note=%r rssi=%s", done["id"], done.get("text"), rssi)


radio_worker.start(_on_packet)
log.info("listening for responder ACKs")


def _send_request(msg_id: str, packet: str) -> None:
    for attempt in range(1, 4):
        state = "sending" if attempt == 1 else "retrying"
        outgoing.set_send_state(msg_id, state, attempt)
        if radio_worker.send(packet):
            outgoing.set_send_state(msg_id, "sent", attempt)
            return
        if attempt < 3:
            time.sleep(1)
    outgoing.set_send_state(msg_id, "failed", 3)


@app.get("/")
def index():
    return render_template("form.html", cats=CATS)


@app.get("/requests")
def requests_page():
    return render_template("requests.html")


@app.post("/send")
def send():
    payload = request.get_json(silent=True) or {}
    if not payload and request.form:
        payload = request.form.to_dict()

    floor = str(payload.get("floor") or FLOOR)
    room = (payload.get("room") or payload.get("zone") or "").strip() or None
    category = str(payload.get("category") or "OTHER").upper()
    text = sanitize(payload.get("text") or "")
    client_id = sanitize(payload.get("client_id") or "")[:40]
    sos = bool(payload.get("sos")) or category == "SOS"
    if sos:
        category = "SOS"
        if not text:
            text = "SOS"

    if not text:
        return jsonify({"ok": False, "error": "Type a short message first."}), 400
    if category not in CATEGORIES:
        category = "OTHER"

    range_m = estimate_range_m(payload.get("rtt_ms"))

    try:
        zone = make_zone(BUILDING, floor, room)
        msg_id = next_id(NODE_ID)
        packet = encode(msg_id, zone, category, text, range_m=range_m)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    outgoing.record(
        msg_id,
        zone,
        category,
        text,
        client_id=client_id or None,
        packet=packet,
    )
    try:
        threading.Thread(
            target=_send_request,
            args=(msg_id, packet),
            name=f"send-{msg_id}",
            daemon=True,
        ).start()
    except Exception as e:
        log.exception("Could not start radio send for %s", msg_id)
        outgoing.set_send_state(msg_id, "failed", 0)
    return jsonify(
        {
            "ok": True,
            "id": msg_id,
            "zone": zone,
            "payload": packet,
            "range_m": range_m,
            "redirect": url_for("status_page", msg_id=msg_id),
        }
    )


@app.get("/status/<msg_id>")
def status_page(msg_id):
    row = outgoing.get_status(msg_id.upper())
    if not row:
        return "Request not found", 404
    return render_template("status.html", msg_id=msg_id.upper())


@app.get("/api/range")
def api_range():
    """Tiny ping so the phone can measure Wi-Fi round-trip to this help-point Wio."""
    return jsonify({"ok": True})


@app.get("/api/my-requests")
def api_my_requests():
    client_id = (request.args.get("client_id") or "").strip()
    ids = [x.strip().upper() for x in (request.args.get("ids") or "").split(",") if x.strip()]
    rows = []
    if client_id:
        rows = outgoing.list_for_client(client_id)
    if ids:
        by_id = {r["id"]: r for r in rows}
        for r in outgoing.list_by_ids(ids):
            by_id[r["id"]] = r
        rows = sorted(by_id.values(), key=lambda r: r.get("sent_at") or "", reverse=True)
    return jsonify(rows)


@app.get("/api/ack/<msg_id>")
def api_ack(msg_id):
    row = outgoing.get_one(msg_id.upper())
    if not row:
        return jsonify({"ok": True, "id": msg_id, "acked": False, "known": False})
    return jsonify(
        {
            "ok": True,
            "id": msg_id,
            "known": True,
            "acked": row.get("acked_at") is not None,
            "acked_at": row.get("acked_at"),
            "sent_at": row.get("sent_at"),
            "reply_text": row.get("reply_text") or "",
            "text": row.get("text"),
            "category": row.get("category"),
            "zone": row.get("zone"),
        }
    )


@app.get("/api/status/<msg_id>")
def api_status(msg_id):
    row = outgoing.get_status(msg_id.upper())
    if not row:
        return jsonify({"error": "Request not found"}), 404
    return jsonify(row)


@app.post("/api/status/<msg_id>/retry")
def retry_send(msg_id):
    row = outgoing.get_one(msg_id.upper())
    if not row or row.get("send_state") != "failed" or not row.get("packet"):
        return jsonify({"ok": False, "error": "Request is not ready to retry"}), 409
    outgoing.set_send_state(msg_id.upper(), "sending", 0)
    try:
        threading.Thread(
            target=_send_request,
            args=(msg_id.upper(), row["packet"]),
            name=f"retry-{msg_id.upper()}",
            daemon=True,
        ).start()
    except Exception:
        log.exception("Could not start radio retry for %s", msg_id)
        outgoing.set_send_state(msg_id.upper(), "failed", row["send_attempts"])
        return jsonify({"ok": False, "error": "Could not start radio retry"}), 500
    return jsonify({"ok": True})


@app.get("/sent/<msg_id>")
def sent(msg_id):
    return redirect(url_for("requests_page") + f"?focus={msg_id}")


@app.get("/api/building")
def building():
    path = Path(app.root_path) / "static" / "floorplans" / f"{BUILDING}.json"
    with path.open(encoding="utf-8") as fh:
        return jsonify(json.load(fh))


@app.get("/health")
def health():
    return jsonify(radio_worker.health())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)
