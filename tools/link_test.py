"""Send N packets from one board and count what the other receives.

  python tools/link_test.py COM7 COM8 [count] [text_len]
  python tools/link_test.py --sim [count]          (FakeRadio, no boards)
"""
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocol import MAX_TEXT, encode  # noqa: E402
from radio import FakeRadio, WioE5  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--sim":
        tx = rx = FakeRadio()
        count = int(args[1]) if len(args) > 1 else 10
        text_len = MAX_TEXT
    else:
        tx, rx = WioE5(args[0]), WioE5(args[1])
        count = int(args[2]) if len(args) > 2 else 10
        text_len = int(args[3]) if len(args) > 3 else MAX_TEXT

    got: dict[str, tuple] = {}

    def on_packet(payload, rssi, snr):
        got[payload.split("|")[0]] = (rssi, snr)
        print(f"  RX {payload!r} rssi={rssi} snr={snr}")

    threading.Thread(target=rx.listen, args=(on_packet,), daemon=True).start()
    time.sleep(1.5)

    sent_ok = 0
    for i in range(1, count + 1):
        payload = encode(f"T{i}", "DUB-3-F14", "MED", ("insulin fridge test " * 10)[:text_len])
        ok = tx.send(payload)
        sent_ok += ok
        print(f"TX {i}/{count} ok={ok} len={len(payload)}")
        time.sleep(0.5)
    time.sleep(2)

    rssis = [r for r, _ in got.values() if r is not None]
    print(f"\nsent {sent_ok}/{count}, received {len(got)}/{count}", end="")
    print(f", RSSI avg {sum(rssis) / len(rssis):.0f} dBm" if rssis else "")


if __name__ == "__main__":
    main()
