import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ROOT / "deps"
if DEPENDENCIES.is_dir():
    sys.path.insert(0, str(DEPENDENCIES))

import watch_push

RELEASE_CONFIG = ROOT / "release.json"
RELEASE = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8")) if RELEASE_CONFIG.exists() else {}
DB = Path(os.environ.get("REACALCUL_DB", RELEASE.get("database", ROOT / "reacalcul.sqlite")))
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
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}
SERIAL_BOUNDS = r"A[0-9]{4}\s+(?:and|to|through)\s+A[0-9]{4}\b"
RANGE_BOUNDS = re.compile(SERIAL_BOUNDS, re.I)
REGEX = re.compile(
    rf"\b(EFMIS[0-9]+)\s+(?:between|date\s+codes?)\s+"
    rf"({SERIAL_BOUNDS}(?:(?:\s*,\s*|\s+(?:and|or)\s+){SERIAL_BOUNDS})*)",
    re.I,
)
MODEL = re.compile(r"[A-Z0-9][A-Z0-9-]{1,23}")
SERIAL = re.compile(r"[A-Z0-9][A-Z0-9-]{0,31}")
RANGE_SERIAL = re.compile(r"A[0-9]{4}")
MAX_BODY = 2048
MAX_SOURCE = 1_000_000
REVISION = RELEASE.get("revision", "local")
SOURCE_TIMEOUT = 12
MANUAL_REFRESH_INTERVAL = 900
SCHEDULED_REFRESH_INTERVAL = 23 * 3600 + 50 * 60
REFUND_URL = "https://www.recallrtr.com/minifridge"
WATCH_KEY = RELEASE.get("watch_key", "")
LIVE_LOCK = threading.Lock()


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
        name = match.group(1).upper()
        for bounds in RANGE_BOUNDS.finditer(match.group(2)):
            start, _, end = bounds.group().upper().split()
            if start > end:
                raise ValueError("Official range order is ambiguous")
            rules.append((name, start, end, match.group(0)))
    return rules


def range_fields(rules):
    by_model = {}
    for name, start, end, _ in rules:
        by_model.setdefault(name, []).append([start, end])
    # Preserve the single-range field so a rolled-back release can read the snapshot.
    return {
        "ranges": {name: pairs[0] for name, pairs in by_model.items() if len(pairs) == 1},
        "multiple_ranges": {name: pairs for name, pairs in by_model.items() if len(pairs) > 1},
    }


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


def archived_records():
    source = json.loads((ROOT / "cpsc-notices.json").read_text(encoding="utf-8"))
    return {id_for(item): item for item in source}


def load_replay():
    verified = ROOT / "verified-replay.json"
    if verified.exists():
        return json.loads(verified.read_text(encoding="utf-8"))
    records = archived_records()
    return [compile_notice(records[key]) for key in ISSUES]


def matching(notice, model, serial):
    outside = None
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
                "quote": quote,
            }
        if start <= serial <= end:
            return {
                "status": "covered",
                "reason": f"Your saved {model} with code {serial} falls within the listed range.",
                "source": notice["source"],
                "evidence": evidence,
                "quote": quote,
            }
        if outside is None:
            outside = {
                "status": "outside_serial",
                "reason": f"Your saved code {serial} falls outside the listed range for {model}.",
                "source": notice["source"],
                "evidence": evidence,
                "quote": quote,
            }
    if outside:
        return outside
    return {
        "status": "outside_model",
        "reason": f"{model} is outside this notice’s model list.",
        "source": notice["source"],
    }


@contextmanager
def database():
    connection = sqlite3.connect(DB, timeout=8)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
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
        connection.execute("""
            CREATE TABLE IF NOT EXISTS feed_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                records TEXT NOT NULL,
                checked_at INTEGER NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS feed_changes (
                id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                previous TEXT NOT NULL,
                notice TEXT NOT NULL,
                detected_at INTEGER NOT NULL,
                PRIMARY KEY(id, fingerprint)
            )
        """)
        watch_push.initialize(connection)
        connection.execute("""
            DELETE FROM checks WHERE updated_at < ?
            AND session NOT IN (SELECT session FROM push_subscriptions)
        """, (int(time.time()) - 365 * 86400,))


def read_state(connection, session):
    row = connection.execute("SELECT * FROM checks WHERE session = ?", (session,)).fetchone()
    if not row:
        return {}
    state = replay_state(connection, row["model"], row["serial"], row["expanded"], row["updated_at"])
    state["notifications"] = watch_push.status(connection, session)
    return state


def replay_state(connection, model, serial, expanded, saved_at):
    baseline = matching(REPLAY[0], model, serial)
    expansion = matching(REPLAY[1], model, serial) if expanded else None
    if baseline["status"] == "covered":
        change = "already_covered"
    elif expansion and expansion["status"] == "covered":
        change = "newly_covered"
    elif expansion and expansion["status"] == "needs_serial":
        change = "needs_serial"
    else:
        change = "no_new_match"
    return {
        "appliance": {"model": model, "serial": serial},
        "baseline": baseline,
        "expansion": expansion,
        "newly_covered": change == "newly_covered",
        "change": change,
        "watch": watch_status(connection, model, serial, saved_at) if saved_at is not None else None,
        "saved_at": saved_at,
        "preview": saved_at is None,
        "control": matching(REPLAY[1], "EFMIS121", "A2401") if expansion and (model, serial) == ("EFMIS121", "A2210") else None,
    }


def feed_snapshot(connection):
    return connection.execute("SELECT records, checked_at FROM feed_state WHERE id = 1").fetchone()


def feed_record(item):
    source = item["URL"]
    if not source.startswith(("https://www.cpsc.gov/Recalls/", "https://cpsc.gov/Recalls/")):
        raise ValueError("Unexpected CPSC link")
    description = " ".join(item["Description"].split())
    remedies = [" ".join(entry["Name"].split()) for entry in item.get("Remedies", [])]
    core = {"title": item["Title"], "source": source, "scope": description, "remedies": remedies}
    fingerprint = hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
    scope = dict(core, scope=REGEX.sub("", description))
    scope_fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    try:
        rows = model_rules(description)
    except ValueError:
        rows = []
    return {
        "id": id_for(item),
        "date": date.fromisoformat(item["RecallDate"][:10]).isoformat(),
        "title": item["Title"],
        "source": source,
        "models": sorted(set(re.findall(r"\bEFMIS[0-9]+(?:-[A-Z0-9]+)?\b", description.upper()))),
        **range_fields(rows),
        "remedy": REFUND_URL if id_for(item) in ISSUES and any('refund' in remedy.lower() for remedy in remedies) else None,
        "fingerprint": fingerprint,
        "scope_fingerprint": scope_fingerprint,
    }


def record_feed(connection, records, checked_at):
    snapshot = feed_snapshot(connection)
    previous_records = json.loads(snapshot["records"]) if snapshot else []
    initialized = bool(previous_records) and all(item.get("fingerprint") for item in previous_records)
    if initialized:
        watch_push.queue_changes(connection, previous_records, records, checked_at)
        previous = {item["id"]: item for item in previous_records}
        for item in records:
            before = previous.get(item["id"])
            if before and before["fingerprint"] == item["fingerprint"]:
                continue
            connection.execute("""
                INSERT INTO feed_changes(id, fingerprint, previous, notice, detected_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id, fingerprint) DO UPDATE SET previous=excluded.previous,
                    notice=excluded.notice, detected_at=excluded.detected_at
            """, (item["id"], item["fingerprint"], json.dumps(before), json.dumps(item), checked_at))
    connection.execute("""
        INSERT INTO feed_state(id, records, checked_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET records=excluded.records, checked_at=excluded.checked_at
    """, (json.dumps(records), checked_at))
    connection.execute("DELETE FROM feed_changes WHERE detected_at < ?", (checked_at - 30 * 86400,))


def refresh_feed(interval):
    with LIVE_LOCK:
        with database() as connection:
            last = feed_snapshot(connection)
        now = int(time.time())
        current_schema = last and all(item.get("fingerprint") for item in json.loads(last["records"]))
        if current_schema and now - last["checked_at"] < interval:
            return {"checked_at": last["checked_at"], "refreshed": False}
        payload = http_json(API)
        if not isinstance(payload, list):
            raise ValueError("Unexpected CPSC response")
        records = [feed_record(item) for item in payload if isinstance(item, dict) and is_frigidaire(item)]
        if not set(ISSUES).issubset(record["id"] for record in records):
            raise ValueError("CPSC returned an incomplete result")
        with database() as connection:
            record_feed(connection, records, now)
        return {"checked_at": now, "refreshed": True}


def matching_change(connection, model, serial, saved_at):
    changes = connection.execute(
        """SELECT previous, notice FROM feed_changes WHERE detected_at >= ?
           ORDER BY detected_at DESC, rowid DESC""", (saved_at,),
    )
    for row in changes:
        before = json.loads(row["previous"])
        after = json.loads(row["notice"])
        previous_models = before["models"] if before else []
        if after["models"] and model not in after["models"] and model not in previous_models:
            continue
        before_ranges = watch_push.ranges_for(before, model) if before else []
        after_ranges = watch_push.ranges_for(after, model)
        if before and before.get("scope_fingerprint") == after.get("scope_fingerprint") and before_ranges == after_ranges:
            continue
        result = {
            "summary": "A watched notice changed. Review the official text." if before else "A new notice may apply. Review the official scope.",
            "source": after["source"],
            "title": after["title"],
            "remedy": after.get("remedy"),
        }
        if before_ranges != after_ranges:
            result["before_range"] = before_ranges[0] if len(before_ranges) == 1 else before_ranges or None
            result["after_range"] = after_ranges[0] if len(after_ranges) == 1 else after_ranges or None
            if RANGE_SERIAL.fullmatch(serial) and watch_push.matching_range(after, model, serial):
                previous_coverage = watch_push.coverage(before, model, serial) if before else False
                if previous_coverage is None:
                    result["summary"] = "The revised range includes your code. Review the official notice to confirm previous coverage."
                elif previous_coverage is False:
                    result["summary"] = "Your saved code falls in a newly listed range. Review the full official notice."
                else:
                    result["summary"] = "Your saved code remains in the revised range. Review the full official notice."
        return result
    return None


def watch_status(connection, model, serial, saved_at):
    snapshot = feed_snapshot(connection)
    if not snapshot:
        return {"summary": "The current CPSC feed check is waiting for its first successful scan."}
    result = {
        "checked_at": datetime.fromtimestamp(snapshot["checked_at"], timezone.utc).isoformat(),
        "summary": "No relevant new or edited notice has been detected since this label was saved.",
    }
    change = matching_change(connection, model, serial, saved_at)
    if change:
        result.update(change)
    elif RANGE_SERIAL.fullmatch(serial):
        for notice in json.loads(snapshot["records"]):
            bounds = watch_push.matching_range(notice, model, serial)
            if not bounds:
                continue
            start, end = bounds
            result.update({
                "summary": f"The latest CPSC scan lists your code in the {start}–{end} recall range.",
                "source": notice["source"],
                "title": notice["title"],
                "remedy": notice.get("remedy"),
            })
            break
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "reacalcul"
    sys_version = ""

    def push_action(self, session, payload):
        action = payload.get("action")
        if action == "example":
            subscription = watch_push.validate_subscription(payload.get("subscription"))
            with database() as connection:
                allowed = watch_push.allow_demo(connection, subscription["endpoint"], int(time.time()))
                key = watch_push.vapid_key(connection) if allowed else None
            if not allowed:
                self.json_response({"error": "An example was requested recently. Try again in a minute."}, 429)
                return
            example = historical_notification()
            result = watch_push.deliver(subscription, example, key)
            if not 200 <= result < 300:
                self.json_response({
                    "error": "The browser push service could not accept the example. Try again in a minute.",
                    "expired": result in (404, 410),
                }, 502)
                return
            self.json_response({"accepted": True})
            return

        with watch_push.DELIVERY_LOCK, database() as connection:
            state = read_state(connection, session)
            if not state:
                self.json_response({"error": "Save your fridge label before enabling device alerts."}, 409)
                return
            if action == "enable":
                model = state["appliance"]["model"]
                serial = state["appliance"]["serial"]
                if not re.fullmatch(r"EFMIS[0-9]+(?:-[A-Z0-9]+)?", model) or not RANGE_SERIAL.fullmatch(serial):
                    self.json_response({"error": "Enter a supported EFMIS model and the five-character code on the label."}, 400)
                    return
                subscription = watch_push.validate_subscription(payload.get("subscription"))
                watch_push.subscribe(connection, session, subscription)
            elif action == "disable":
                watch_push.unsubscribe(connection, session)
            else:
                self.json_response({"error": "Choose a notification action."}, 400)
                return
            notifications = watch_push.status(connection, session)
        self.json_response(notifications, cookie=session)

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
            self.send_header("Set-Cookie", f"reacalcul={cookie}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")
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
        path = urlsplit(self.path).path
        if path in ASSETS:
            name, content_type = ASSETS[path]
            self.send_bytes(200, (ROOT / name).read_bytes(), content_type)
        elif self.path == "/healthz":
            self.json_response({
                "ok": True,
                "connector_verified": all(item["parsed_via"] == "Sprites CPSC connector" for item in REPLAY),
                "revision": REVISION,
            })
        elif self.path == "/api/state":
            session = self.cookie()
            with database() as connection:
                state = read_state(connection, session)
            self.json_response(state, cookie=session if state else None)
        elif self.path == "/api/push/key":
            with database() as connection:
                key = watch_push.public_key(connection)
            self.json_response({"key": key})
        else:
            self.json_response({"error": "Page not found."}, 404)

    def do_POST(self):
        if self.path == "/api/feed/refresh":
            supplied = self.headers.get("Authorization", "").encode()
            expected = ("Bearer " + WATCH_KEY).encode()
            if not WATCH_KEY or not secrets.compare_digest(supplied, expected):
                self.json_response({"error": "Scheduled refresh credentials required."}, 401)
                return
            try:
                status = refresh_feed(SCHEDULED_REFRESH_INTERVAL)
            except (OSError, ValueError, KeyError, TypeError):
                watch_push.dispatch(database)
                self.json_response({"error": "The CPSC feed refresh failed. The last successful scan is retained."}, 502)
            else:
                status["alerts_accepted"] = watch_push.dispatch(database)
                self.json_response(status)
            return
        if self.path not in ("/api/check", "/api/demo", "/api/replay", "/api/live", "/api/push"):
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
        if self.path == "/api/push":
            try:
                self.push_action(session, payload)
            except ValueError as error:
                self.json_response({"error": str(error)}, 400)
            return
        if self.path == "/api/demo":
            serial = payload.get("serial", "A2210")
            if serial not in ("A2210", "A2401"):
                self.json_response({"error": "Choose one of the example serials."}, 400)
                return
            with database() as connection:
                state = replay_state(connection, "EFMIS121", serial, True, None)
            if state["newly_covered"]:
                state["alert_example"] = historical_notification()
            self.json_response(state)
            return
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
            expanded = int(payload.get("current") is True)
            new_session = session or secrets.token_urlsafe(32)
            with watch_push.DELIVERY_LOCK, database() as connection:
                previous = connection.execute(
                    "SELECT model, serial FROM checks WHERE session = ?", (new_session,)
                ).fetchone()
                if previous and tuple(previous) != (model, serial):
                    connection.execute("DELETE FROM push_deliveries WHERE session = ?", (new_session,))
                    if not re.fullmatch(r"EFMIS[0-9]+(?:-[A-Z0-9]+)?", model) or not RANGE_SERIAL.fullmatch(serial):
                        watch_push.unsubscribe(connection, new_session)
                connection.execute("""
                    INSERT INTO checks(session, model, serial, expanded, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session) DO UPDATE SET model=excluded.model, serial=excluded.serial,
                        expanded=excluded.expanded, updated_at=CASE
                            WHEN checks.model=excluded.model AND checks.serial=excluded.serial THEN checks.updated_at
                            ELSE excluded.updated_at END
                """, (new_session, model, serial, expanded, int(time.time())))
                state = read_state(connection, new_session)
            self.json_response(state, cookie=new_session)
            return
        if self.path == "/api/replay":
            with database() as connection:
                changed = connection.execute(
                    "UPDATE checks SET expanded = 1 WHERE session = ? AND expanded = 0", (session,)
                ).rowcount
                state = read_state(connection, session)
            if not state:
                self.json_response({"error": "Check the July notice first."}, 409)
                return
            state["recorded"] = bool(changed and state["newly_covered"])
            self.json_response(state)
            return
        with database() as connection:
            state = read_state(connection, session)
        if not state:
            self.json_response({"error": "Check the July notice first."}, 409)
            return
        try:
            refresh_feed(MANUAL_REFRESH_INTERVAL)
            watch_push.dispatch(database, limit=2)
            with database() as connection:
                self.json_response(watch_status(connection, state["appliance"]["model"], state["appliance"]["serial"], state["saved_at"]))
        except (OSError, URLError, ValueError, KeyError, TypeError):
            self.json_response({"error": "The CPSC feed is temporarily unavailable. The historical results are still available."}, 502)


def historical_notification():
    model, serial = "EFMIS121", "A2210"
    notice = REPLAY[1]
    rule = matching(notice, model, serial)
    if rule["status"] != "covered" or matching(REPLAY[0], model, serial)["status"] == "covered":
        raise ValueError("The historical example is no longer available.")
    alert_notice = {**notice, **range_fields(notice["rules"]), "remedy": REFUND_URL}
    return watch_push.notification(model, serial, alert_notice, example=True)


def import_replay():
    records = {id_for(item): item for item in http_json(API) if is_frigidaire(item)}
    archive = archived_records()
    for key in ISSUES:
        current = records[key]
        for field in ("URL", "RecallDate"):
            if archive[key][field] != current[field]:
                raise ValueError(f"Official identity of notice {key} changed; review the historical archive.")
    replay = [compile_notice(archive[key], "Sprites CPSC connector") for key in ISSUES]
    output = ROOT / "verified-replay.json"
    temp = output.with_suffix(".tmp")
    temp.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)
    print(json.dumps({"records": [notice["id"] for notice in replay], "via": replay[0]["parsed_via"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "import", "check"), nargs="?", default="serve")
    arguments = parser.parse_args()
    if arguments.command == "import":
        if not CPSC_CONNECTOR:
            parser.error("Set REACALCUL_CPSC_CONNECTOR_URL before importing.")
        import_replay()
    else:
        REPLAY = load_replay()
        init_db()
        if arguments.command == "serve":
            port = int(os.environ.get("PORT", "8080"))
            ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
