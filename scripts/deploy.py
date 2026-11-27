import concurrent.futures
import http.cookiejar
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

API = "https://api.sprites.dev/v1"
NAME = "reacalcul"
SPRITE = f"/sprites/{NAME}"
TOKEN = os.environ["SPRITES_TOKEN"]
CONNECTOR = os.environ["CPSC_CONNECTOR_URL"]
WATCH_KEY = os.environ["WATCH_REFRESH_KEY"]
REVISION = os.environ["GITHUB_SHA"]
SOURCE = Path(__file__).resolve().parents[1]
FILES = ("app.py", "index.html", "app.css", "app.js", "sw.js", "manifest.webmanifest", "cpsc-notices.json", "watch_push.py", "requirements.txt")

if not TOKEN or not re.fullmatch(r"[0-9a-f]{40}", REVISION):
    raise SystemExit("GitHub deployment needs SPRITES_TOKEN and a valid commit revision.")
if not re.fullmatch(r"https://api\.sprites\.dev/v1/gateway/custom_api/[A-Za-z0-9_-]+", CONNECTOR):
    raise SystemExit("Set CPSC_CONNECTOR_URL to the CPSC Custom API gateway base URL.")

if not re.fullmatch(r"[A-Za-z0-9_-]{43}", WATCH_KEY):
    raise SystemExit("Set WATCH_REFRESH_KEY to a random 32-byte URL-safe key.")

RELEASE = f"/home/sprite/reacalcul-releases/{REVISION}"
SERVICE_ROOT = "/home/sprite/reacalcul"


def platform(path, method="GET", payload=None, *, raw=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if raw is not None:
        headers["Content-Type"] = "application/octet-stream"
        data = raw
    try:
        with urlopen(Request(API + path, data=data, headers=headers, method=method), timeout=100) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"Sprites {method} {path.split('?')[0]} returned HTTP {error.code}") from None


def upload_bytes(name, data, mode="0644"):
    query = urlencode({"path": name, "workingDir": RELEASE, "mkdir": "true", "mode": mode})
    platform(f"{SPRITE}/fs/write?{query}", "PUT", raw=data)


def upload(name):
    upload_bytes(name, (SOURCE / name).read_bytes())


def run_on_sprite(*args):
    query = urlencode([("cmd", part) for part in args])
    response = platform(f"{SPRITE}/exec?{query}", "POST")
    if len(response) < 2 or response[-2:] != bytes((3, 0)):
        output = response[:-2].replace(bytes((1,)), b"").replace(bytes((2,)), b"")
        raise RuntimeError("Sprite command failed: " + output.decode(errors="replace")[-1600:])


def public_json(opener, base_url, path, payload=None, *, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(base_url + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with opener.open(req, timeout=18) as response:
        return json.load(response)


def verify(url):
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    last_health = {}
    for _ in range(12):
        try:
            last_health = public_json(opener, url, "/healthz")
            if last_health == {"ok": True, "connector_verified": True, "revision": REVISION}:
                break
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(5)
    else:
        raise RuntimeError(f"Release did not pass the public health check: {last_health}")
    demo = public_json(opener, url, "/api/demo", {})
    assert demo["change"] == "newly_covered"
    assert demo["preview"] and demo["control"]["status"] == "outside_serial"
    example = demo["alert_example"]
    assert example["example"] and example["range"] == ["A2001", "A2310"]
    assert public_json(opener, url, "/api/demo", {"serial": "A2401"})["change"] == "no_new_match"
    assert re.fullmatch(r"[A-Za-z0-9_-]{87}", public_json(opener, url, "/api/push/key")["key"])
    with opener.open(url + "/sw.js", timeout=18) as worker:
        assert worker.status == 200 and b"notificationclick" in worker.read()
    assert public_json(opener, url, "/api/state") == {}
    july = public_json(opener, url, "/api/check", {"model": "EFMIS121", "serial": "A2210"})
    january = public_json(opener, url, "/api/replay", {})
    restored = public_json(opener, url, "/api/state")
    repeated = public_json(opener, url, "/api/replay", {})
    second = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    isolated = public_json(second, url, "/api/state")
    public_json(second, url, "/api/check", {"model": "EFMIS121", "serial": "A2401"})
    control = public_json(second, url, "/api/replay", {})
    saved = public_json(second, url, "/api/state")
    public_json(second, url, "/api/demo", {})
    assert public_json(second, url, "/api/state") == saved
    assert july["baseline"]["status"] == "outside_model" and not july["notifications"]["enabled"]
    assert july["watch"] is not None
    checked = public_json(second, url, "/api/check", {"model": "EFMIS121", "serial": "A2210", "current": True})
    assert checked["expansion"]["status"] == "covered"
    with opener.open(url + "/?alert=example", timeout=18) as page:
        assert page.status == 200 and b"Replay a real recall" in page.read()
    assert january["newly_covered"] and january["expansion"]["status"] == "covered"
    assert january["expansion"]["quote"] == "EFMIS121 Between A2001 and A2310"
    assert january["recorded"] and not repeated["recorded"]
    assert restored["expansion"] == january["expansion"] and isolated == {}
    assert control["expansion"]["status"] == "outside_serial" and not control["newly_covered"]
    for model, serial, status, change in (
        ("EFMIS121", "", "needs_serial", "needs_serial"),
        ("EFMIS129", "A2210", "outside_model", "already_covered"),
        ("EFMIS129-B", "A2210", "outside_model", "no_new_match"),
    ):
        public_json(second, url, "/api/check", {"model": model, "serial": serial})
        case = public_json(second, url, "/api/replay", {})
        assert case["expansion"]["status"] == status and case["change"] == change
    try:
        public_json(opener, url, "/api/feed/refresh", {})
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("The scheduled endpoint accepted a request without authorization.")
    refresh = public_json(opener, url, "/api/feed/refresh", {}, headers={"Authorization": f"Bearer {WATCH_KEY}"})
    live = public_json(opener, url, "/api/live", {})
    assert refresh["checked_at"] and live["checked_at"]
    print("Verified the release, connector import, replay deduplication, both outcomes, session isolation, and scheduled feed authorization, and the public Web Push demo and worker.")


def main():
    try:
        sprite_info = json.loads(platform(SPRITE))
    except RuntimeError as error:
        if not str(error).endswith("HTTP 404"):
            raise
        sprite_info = json.loads(platform("/sprites", "POST", {"name": NAME}))
    url = sprite_info["url"].rstrip("/")
    if not re.fullmatch(r"https://reacalcul-[A-Za-z0-9-]+\.sprites\.app", url):
        raise RuntimeError("Sprites returned an unexpected public URL.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(upload, FILES))
    config = json.dumps({"connector": CONNECTOR, "revision": REVISION, "watch_key": WATCH_KEY}).encode()
    upload_bytes("release.json", config, "0600")
    dependencies = f"{RELEASE}/deps"
    upload_bytes("deps.tar.gz", Path(os.environ["PUSH_DEPENDENCIES"]).read_bytes())
    run_on_sprite("mkdir", "-p", dependencies)
    run_on_sprite("tar", "-xzf", f"{RELEASE}/deps.tar.gz", "-C", dependencies)
    run_on_sprite("env", f"PYTHONPATH={dependencies}", "python3", f"{RELEASE}/app.py", "import")
    service = f"{SPRITE}/services/web"
    run_on_sprite("mkdir", "-p", SERVICE_ROOT)
    run_on_sprite("ln", "-sfn", f"{RELEASE}/app.py", f"{SERVICE_ROOT}/incoming.py")
    run_on_sprite("mv", "-Tf", f"{SERVICE_ROOT}/incoming.py", f"{SERVICE_ROOT}/app.py")
    platform(service, "PUT", {
        "cmd": "python3",
        "args": [f"{SERVICE_ROOT}/app.py", "serve"],
        "dir": SERVICE_ROOT,
        "needs": [],
        "http_port": 8080,
        "env": {
            "PYTHONPATH": dependencies,
            "REACALCUL_DB": "/home/sprite/reacalcul-data/reacalcul.sqlite",
        },
    })
    platform(service + "/restart", "POST")
    platform(SPRITE, "PUT", {"url_settings": {"auth": "public"}})
    verify(url)
    print(f"Public demo: {url}")


if __name__ == "__main__":
    main()
