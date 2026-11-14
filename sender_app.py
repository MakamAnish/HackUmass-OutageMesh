"""Laptop A (help point): form -> LoRa; status page; listen for responder ACKs."""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for

import outgoing
import radio_worker
from protocol import CATEGORIES, decode_ack, decode_done, encode, make_zone, next_id, sanitize

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
    done = decode_done(payload)
    if done is not None:
        outgoing.mark_resolved(done["id"], done.get("text") or None)
        log.info("DONE for %s note=%r rssi=%s", done["id"], done.get("text"), rssi)
        return
    ack = decode_ack(payload)
    if ack is None:
        return
    outgoing.mark_acked(ack["id"], ack.get("text") or None)
    log.info("ACK for %s reply=%r rssi=%s", ack["id"], ack.get("text"), rssi)


radio_worker.start(_on_packet)
log.info("listening for responder ACKs")


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

    if not text:
        return jsonify({"ok": False, "error": "Type a short message first."}), 400
    if category not in CATEGORIES:
        category = "OTHER"

    try:
        zone = make_zone(BUILDING, floor, room)
        msg_id = next_id(NODE_ID)
        packet = encode(msg_id, zone, category, text)
        ok = radio_worker.send(packet)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        log.exception("send failed")
        return jsonify({
            "ok": False,
            "error": f"Radio error: {e}. Check RADIO_PORT in .env and that the Wio is plugged in.",
        }), 500

    if not ok:
        err = radio_worker.health().get("error") or "Radio did not confirm TX DONE."
        return jsonify({
            "ok": False,
            "error": f"{err} Check the board on {os.getenv('RADIO_PORT', '?')} (close Arduino Serial Monitor).",
        }), 500

    outgoing.record(msg_id, zone, category, text, client_id=client_id or None)
    return jsonify(
        {
            "ok": True,
            "id": msg_id,
            "zone": zone,
            "payload": packet,
            "redirect": url_for("requests_page") + f"?focus={msg_id}",
        }
    )


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
