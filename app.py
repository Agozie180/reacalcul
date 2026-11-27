import argparse
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("REACALCUL_DB", ROOT / "reacalcul.sqlite"))
RELEASE_CONFIG = ROOT / "release.json"
RELEASE = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8")) if RELEASE_CONFIG.exists() else {}
CPSC_CONNECTOR = os.environ.get("REACALCUL_CPSC_CONNECTOR_URL", RELEASE.get("connector", ""))
FEED_BASE = CPSC_CONNECTOR.rstrip("/") or "https://www.saferproducts.gov"
API = FEED_BASE + "/RestWebServices/Recall?" + urlencode({
    "format": "json", "RecallTitle": "Curtis International", "RecallDateStart": "2025-01-01",
})
ISSUES = ("25395", "26199")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
REGEX = re.compile(r"\b(EFMIS[0-9]+)\s+Between\s+(A[0-9]{4})\s+(?:and|to)\s+(A[0-9]{4})\b", re.I)
MODEL = re.compile(r"[A-Z0-9][A-Z0-9-]{1,23}")
SERIAL = re.compile(r"[A-Z0-9][A-Z0-9-]{0,31}")
RANGE_SERIAL = re.compile(r"A[0-9]{4}")
MAX_BODY = 2048
MAX_SOURCE = 1_000_000
REVISION = RELEASE.get("revision", "local")
SOURCE_TIMEOUT = 12
LIVE_INTERVAL = 300
LIVE_LOCK = threading.Lock()
LIVE_CACHE = None


def http_json(url, timeout=SOURCE_TIMEOUT):
    headers = {"Accept": "application/json", "User-Agent": "reacalcul/1.0"}
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        body = response.read(MAX_SOURCE + 1)
        if len(body) > MAX_SOURCE:
            raise ValueError("Source response exceeds size limit")
        return json.loads(body)


def id_for(record):
    return str(record.get("RecallNumber", "")).replace("-", "")


def is_frigidaire(record):
    title = str(record.get("Title", "")).lower()
    return all(term in title for term in ("curtis international", "frigidaire", "minifridge"))


def model_rules(description):
    rules = []
    for match in REGEX.finditer(description):
        name, start, end = (value.upper() for value in match.groups())
        if start > end:
            raise ValueError("Official range order is ambiguous")
        rules.append((name, start, end, match.group(0)))
    return rules


def compile_notice(record, parsed_via="bundled CPSC notice"):
    description = record["Description"]
    source = record["URL"]
    if not is_frigidaire(record) or not source.startswith(("https://www.cpsc.gov/Recalls/", "https://cpsc.gov/Recalls/")):
        raise ValueError("Unexpected recall source")
    rules = model_rules(description)
    if not rules:
        raise ValueError("The notice has no supported serial ranges")
    return {
        "id": id_for(record),
        "date": date.fromisoformat(record["RecallDate"][:10]).isoformat(),
        "title": record["Title"],
        "source": source,
        "rules": rules,
        "parsed_via": parsed_via,
    }


def load_replay():
    verified = ROOT / "verified-replay.json"
    if verified.exists():
        return json.loads(verified.read_text(encoding="utf-8"))
    source = json.loads((ROOT / "cpsc-notices.json").read_text(encoding="utf-8"))
    records = {id_for(item): item for item in source}
    return [compile_notice(records[key]) for key in ISSUES]


def matching(notice, model, serial):
    for name, start, end, quote in notice["rules"]:
        if name != model:
            continue
        evidence = f"{name} · {start}–{end}"
        if not RANGE_SERIAL.fullmatch(serial):
            return {
                "status": "needs_serial",
                "reason": f"The table shows codes like {start}. Enter the five-character code shown in the notice format.",
                "source": notice["source"],
                "evidence": evidence,
            }
        if start <= serial <= end:
            return {
                "status": "covered",
                "reason": f"Your saved {model} with code {serial} falls within the listed range.",
                "source": notice["source"],
                "evidence": evidence,
            }
        return {
            "status": "outside_serial",
            "reason": f"Your saved code {serial} falls outside the listed range for {model}.",
            "source": notice["source"],
            "evidence": evidence,
        }
    return {
        "status": "outside_model",
        "reason": f"{model} is outside this notice’s model list.",
        "source": notice["source"],
    }


@contextmanager
def database():
    connection = sqlite3.connect(DB, timeout=8)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS checks (
                session TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                serial TEXT NOT NULL,
                expanded INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
        """)
        connection.execute("DELETE FROM checks WHERE updated_at < ?", (int(time.time()) - 30 * 86400,))


def read_state(connection, session):
    row = connection.execute("SELECT * FROM checks WHERE session = ?", (session,)).fetchone()
    if not row:
        return {}
    model, serial = row["model"], row["serial"]
    baseline = matching(REPLAY[0], model, serial)
    expansion = matching(REPLAY[1], model, serial) if row["expanded"] else None
    return {
        "appliance": {"model": model, "serial": serial},
        "baseline": baseline,
        "expansion": expansion,
        "newly_covered": bool(expansion and baseline["status"] != "covered" and expansion["status"] == "covered"),
    }


def live_check(appliance):
    global LIVE_CACHE
    with LIVE_LOCK:
        now = time.monotonic()
        if LIVE_CACHE is None or now - LIVE_CACHE[0] >= LIVE_INTERVAL:
            data = http_json(API)
            if not isinstance(data, list):
                raise ValueError("Unexpected CPSC response")
            records = [item for item in data if is_frigidaire(item)]
            LIVE_CACHE = (now, records)
        records = LIVE_CACHE[1]
    known = {id_for(item) for item in records}
    if not set(ISSUES).issubset(known):
        raise ValueError("CPSC returned an incomplete result")
    newer = [item for item in records if item.get("RecallDate", "")[:10] > REPLAY[1]["date"]]
    match = None
    review = False
    for record in newer:
        try:
            notice = compile_notice(record)
            outcome = matching(notice, appliance["model"], appliance["serial"])
        except (KeyError, ValueError):
            review = True
            continue
        if outcome["status"] == "covered":
            match = {"date": notice["date"], "title": notice["title"], **outcome}
        elif outcome["status"] == "needs_serial":
            review = True
    if match:
        summary = "The CPSC feed has a newer notice covering your saved label."
    elif review:
        summary = "A newer notice needs review. Open the CPSC source to check its details."
    else:
        summary = "No newer matching notice appeared in the CPSC results for this recall series."
    return {"summary": summary, "match": match, "source": "https://www.cpsc.gov/Recalls"}


class Handler(BaseHTTPRequestHandler):
    server_version = "reacalcul"
    sys_version = ""

    def send_bytes(self, status, raw, content_type, cookie=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https://www.cpsc.gov; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if cookie:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" or self.headers.get("Host", "").endswith(".sprites.app") else ""
            self.send_header("Set-Cookie", f"reacalcul={cookie}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax{secure}")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def json_response(self, data, status=200, cookie=None):
        self.send_bytes(status, json.dumps(data).encode(), "application/json; charset=utf-8", cookie)

    def cookie(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        item = jar.get("reacalcul")
        if item and re.fullmatch(r"[A-Za-z0-9_-]{43}", item.value):
            return item.value
        return None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.path in ASSETS:
            name, content_type = ASSETS[self.path]
            self.send_bytes(200, (ROOT / name).read_bytes(), content_type)
        elif self.path == "/healthz":
            self.json_response({
                "ok": True,
                "connector_verified": all(item["parsed_via"] == "Sprites CPSC connector" for item in REPLAY),
                "revision": REVISION,
            })
        elif self.path == "/api/state":
            with database() as connection:
                state = read_state(connection, self.cookie())
            self.json_response(state)
        else:
            self.json_response({"error": "Page not found."}, 404)

    def do_POST(self):
        if self.path not in ("/api/check", "/api/replay", "/api/live"):
            self.json_response({"error": "Page not found."}, 404)
            return
        if self.headers.get_content_type() != "application/json":
            self.json_response({"error": "Send a JSON request."}, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            self.json_response({"error": "The form could not be read."}, 400)
            return
        session = self.cookie()
        if self.path == "/api/check":
            raw_model = payload.get("model", "")
            raw_serial = payload.get("serial", "")
            if not isinstance(raw_model, str) or not isinstance(raw_serial, str):
                self.json_response({"error": "Enter the model and serial as text."}, 400)
                return
            model = raw_model.strip().upper()
            serial = raw_serial.strip().upper()
            if not MODEL.fullmatch(model) or serial and not SERIAL.fullmatch(serial):
                self.json_response({"error": "Enter model and serial using letters, digits, or hyphens."}, 400)
                return
            new_session = session or secrets.token_urlsafe(32)
            with database() as connection:
                connection.execute("""
                    INSERT INTO checks(session, model, serial, expanded, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                    ON CONFLICT(session) DO UPDATE SET model=excluded.model, serial=excluded.serial,
                        expanded=0, updated_at=excluded.updated_at
                """, (new_session, model, serial, int(time.time())))
                state = read_state(connection, new_session)
            self.json_response(state, cookie=new_session if not session else None)
            return
        if self.path == "/api/replay":
            with database() as connection:
                connection.execute("UPDATE checks SET expanded = 1 WHERE session = ?", (session,))
                state = read_state(connection, session)
            if not state:
                self.json_response({"error": "Check the July notice first."}, 409)
                return
            self.json_response(state)
            return
        with database() as connection:
            state = read_state(connection, session)
        if not state:
            self.json_response({"error": "Check the July notice first."}, 409)
            return
        try:
            self.json_response(live_check(state["appliance"]))
        except (OSError, URLError, ValueError):
            self.json_response({"error": "The CPSC feed is temporarily unavailable. The historical results are still available."}, 502)


def import_replay():
    records = {id_for(item): item for item in http_json(API) if is_frigidaire(item)}
    replay = [compile_notice(records[key], "Sprites CPSC connector") for key in ISSUES]
    output = ROOT / "verified-replay.json"
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)
    print(json.dumps({"records": [notice["id"] for notice in replay], "via": replay[0]["parsed_via"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "import"), nargs="?", default="serve")
    arguments = parser.parse_args()
    if arguments.command == "import":
        if not CPSC_CONNECTOR:
            parser.error("Set REACALCUL_CPSC_CONNECTOR_URL before importing.")
        import_replay()
    else:
        REPLAY = load_replay()
        init_db()
        port = int(os.environ.get("PORT", "8080"))
        ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
