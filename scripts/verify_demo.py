"""Verify the public Hacksgiving demo using only the Python standard library."""
import json
import re
import statistics
import time
from urllib.request import Request, urlopen

BASE = "https://reacalcul-b623b.sprites.app"


def read(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + path, data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    return data, time.perf_counter() - started


def require(result, message):
    if not result:
        raise SystemExit(f"FAIL: {message}")


def main():
    health, _ = read("/healthz")
    revision = health.get("revision", "")
    require(health.get("ok") and health.get("connector_verified"), "Live Sprite and connector")
    require(re.fullmatch(r"[a-f0-9]{40}", revision), "Live response names an exact git commit")
    print(f"PASS: live Sprite and verified connector on {revision}")

    latencies = []
    for _ in range(3):
        match, elapsed = read("/api/demo", {"serial": "A2210"})
        latencies.append(elapsed)
        control, elapsed = read("/api/demo", {"serial": "A2401"})
        latencies.append(elapsed)
        require(match["baseline"]["status"] == "outside_model" and match["newly_covered"], "A2210 becomes newly covered")
        require(match["expansion"]["quote"] == "EFMIS121 Between A2001 and A2310", "The receipt quotes the exact CPSC rule")
        alert = match["alert_example"]
        require(alert["example"] and alert["range"] == ["A2001", "A2310"], "The sample alert is labeled and carries the exact range")
        require(alert["notice"].startswith("https://www.cpsc.gov/Recalls/2026/") and alert["remedy"], "The sample links to the source and refund")
        require(control["appliance"]["model"] == match["appliance"]["model"], "The control uses the same model")
        require(control["expansion"]["status"] == "outside_serial" and "alert_example" not in control, "A2401 produces no example alert")

    state, _ = read("/api/state")
    require(state == {}, "Replays leave the appliance watch untouched")
    print("PASS: 3/3 replays per label, exact source and refund receipt, no saved watch created")
    print(f"Replay API round trip, this machine: n={len(latencies)}, median {statistics.median(latencies):.2f}s, max {max(latencies):.2f}s")


if __name__ == "__main__":
    main()
