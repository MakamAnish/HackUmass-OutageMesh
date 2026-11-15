"""Estimate phone distance to the help-point Wio (laptop hotspot).

The phone has no LoRa radio and the browser cannot read Wi-Fi RSSI, so a
round-trip HTTP ping to sender_app is the usable signal. Time-of-flight is
not measurable at these ranges; RTT mainly tracks Wi-Fi retries / weak link,
which still grows with distance indoors.
"""

MIN_M = 2.0
MAX_M = 45.0
DEFAULT_M = 18.0
BASELINE_MS = 2.0


def estimate_range_m(rtt_ms: object | None) -> float:
    """Map median ping RTT (ms) to an indoor range in meters."""
    try:
        rtt = float(rtt_ms)
    except (TypeError, ValueError):
        return DEFAULT_M
    if rtt <= 0:
        return DEFAULT_M
    excess = max(0.0, rtt - BASELINE_MS)
    # ~3 m on a strong LAN, ~15 m around 20 ms, hotspot edge near MAX_M.
    meters = 3.0 + 2.4 * (excess ** 0.5)
    return round(max(MIN_M, min(MAX_M, meters)), 1)
