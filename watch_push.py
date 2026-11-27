"""Web Push subscriptions, recall alerts, and delivery receipts."""
import base64
import binascii
import hashlib
import json
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

DELIVERY_LOCK = threading.Lock()
DEMO_WINDOW = 60
PUSH_MAX_AGE = 7 * 86400
PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}
PUSH_DOMAINS = (".notify.windows.com", ".web.push.apple.com")


def initialize(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS push_key (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            private TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            session TEXT PRIMARY KEY REFERENCES checks(session) ON DELETE CASCADE,
            endpoint TEXT NOT NULL UNIQUE,
            subscription TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_deliveries (
            session TEXT NOT NULL REFERENCES push_subscriptions(session) ON DELETE CASCADE,
            event TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            next_attempt INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            accepted_at INTEGER,
            PRIMARY KEY (session, event)
        );
        CREATE TABLE IF NOT EXISTS push_demo_limits (
            endpoint_hash TEXT PRIMARY KEY,
            sent_at INTEGER NOT NULL
        );
    """)
    if connection.execute("SELECT id FROM push_key WHERE id = 1").fetchone() is None:
        vapid = Vapid()
        vapid.generate_keys()
        private = vapid.private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        connection.execute("INSERT OR IGNORE INTO push_key VALUES (1, ?)", (private.decode("ascii"),))


def vapid_key(connection):
    private = connection.execute("SELECT private FROM push_key WHERE id = 1").fetchone()[0]
    key = Vapid.from_pem(private.encode("ascii"))
    # A public HTTPS contact URI is valid under RFC 8292.
    return Vapid(key.private_key, conf={"no-strict": True})


def public_key(connection):
    public = vapid_key(connection).public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def browser_key(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid browser subscription key")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except binascii.Error as error:
        raise ValueError("Invalid browser subscription key") from error
    if len(decoded) != length:
        raise ValueError("Invalid browser subscription key")
    return decoded


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("Missing browser subscription")
    endpoint = value.get("endpoint")
    keys = value.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 1500 or not isinstance(keys, dict):
        raise ValueError("Invalid browser subscription")
    if any(ord(character) <= 32 or ord(character) >= 127 for character in endpoint) or "\\" in endpoint:
        raise ValueError("Invalid browser push endpoint")
    url = urlsplit(endpoint)
    host = url.hostname or ""
    if (
        url.scheme != "https"
        or url.netloc.lower() not in (host, host + ":443")
        or not (host in PUSH_HOSTS or host.endswith(PUSH_DOMAINS))
        or not url.path.startswith("/")
        or url.fragment
    ):
        raise ValueError("This browser push provider is unsupported")
    public = browser_key(keys.get("p256dh"), 65)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    browser_key(keys.get("auth"), 16)
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


def subscribe(connection, session, subscription):
    endpoint = subscription["endpoint"]
    connection.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND session != ?", (endpoint, session))
    connection.execute("""
        INSERT INTO push_subscriptions(session, endpoint, subscription)
        VALUES (?, ?, ?) ON CONFLICT(session) DO UPDATE SET
        endpoint = excluded.endpoint, subscription = excluded.subscription
    """, (session, endpoint, json.dumps(subscription)))


def unsubscribe(connection, session):
    connection.execute("DELETE FROM push_subscriptions WHERE session = ?", (session,))


def status(connection, session):
    subscribed = connection.execute("SELECT 1 FROM push_subscriptions WHERE session = ?", (session,)).fetchone()
    sent = connection.execute("""
        SELECT payload FROM push_deliveries WHERE session = ? AND accepted_at IS NOT NULL
        ORDER BY accepted_at DESC, rowid DESC LIMIT 1
    """, (session,)).fetchone()
    return {"enabled": bool(subscribed), "last_alert": json.loads(sent[0]) if sent else None}


def notification(model, serial, notice, *, example=False):
    start, end = notice["ranges"][model]
    return {
        "title": "Historical example: recall coverage changed" if example else "Recall update: your label is newly listed",
        "body": (
            f"{model} / {serial} is in the {start}–{end} range. "
            "Open for the official notice and next steps."
        ),
        "model": model,
        "serial": serial,
        "range": [start, end],
        "notice": notice["source"],
        "remedy": notice.get("remedy"),
        "example": example,
        "tag": ("example-" if example else "") + str(notice["id"]),
        "url": "/?alert=example" if example else "/?alert=recall",
    }


def coverage(notice, model, serial):
    bounds = notice["ranges"].get(model)
    if bounds:
        return bounds[0] <= serial <= bounds[1]
    models = notice["models"]
    if not models or model in models:
        return None
    return False


def queue_changes(connection, previous, current, checked_at):
    if not previous:
        return
    stored = connection.execute("""
        SELECT checks.session, checks.model, checks.serial FROM checks
        JOIN push_subscriptions ON push_subscriptions.session = checks.session
    """)
    for row in stored:
        session, model, serial = row
        if not re.fullmatch(r"A[0-9]{4}", serial):
            continue
        if any(coverage(notice, model, serial) is not False for notice in previous):
            continue
        for notice in current:
            if coverage(notice, model, serial) is not True:
                continue
            event = f"{notice['id']}:{notice['fingerprint']}:{model}:{serial}"
            connection.execute("""
                INSERT OR IGNORE INTO push_deliveries(session, event, payload, created_at, next_attempt)
                VALUES (?, ?, ?, ?, ?)
            """, (session, event, json.dumps(notification(model, serial, notice)), checked_at, checked_at))
            break


class DirectSession(requests.Session):
    def post(self, url, **kwargs):
        return super().post(url, allow_redirects=False, **kwargs)


def deliver(subscription, payload, key):
    endpoint = urlsplit(subscription["endpoint"])
    topic = hashlib.sha256(payload["tag"].encode()).hexdigest()[:32]
    try:
        with DirectSession() as client:
            response = webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=key,
                vapid_claims={"aud": f"{endpoint.scheme}://{endpoint.netloc}", "sub": "https://reacalcul-b623b.sprites.app/"},
                ttl=60 if payload["example"] else 86400,
                headers={"Urgency": "high", "Topic": topic},
                timeout=2,
                requests_session=client,
            )
        return response.status_code
    except WebPushException as error:
        return error.response.status_code if error.response is not None else 0
    except (requests.RequestException, ValueError):
        return 0


def allow_demo(connection, endpoint, now):
    connection.execute("DELETE FROM push_demo_limits WHERE sent_at <= ?", (now - DEMO_WINDOW,))
    if connection.execute("SELECT COUNT(*) FROM push_demo_limits").fetchone()[0] >= 30:
        return False
    key = hashlib.sha256(endpoint.encode()).hexdigest()
    return bool(connection.execute("INSERT OR IGNORE INTO push_demo_limits VALUES (?, ?)", (key, now)).rowcount)


def dispatch(database, limit=4):
    with DELIVERY_LOCK:
        now = int(time.time())
        with database() as connection:
            connection.execute("DELETE FROM push_deliveries WHERE accepted_at IS NULL AND created_at < ?", (now - PUSH_MAX_AGE,))
            due = connection.execute("""
                SELECT push_deliveries.*, push_subscriptions.subscription FROM push_deliveries
                JOIN push_subscriptions USING (session)
                WHERE accepted_at IS NULL AND next_attempt <= ?
                ORDER BY next_attempt LIMIT ?
            """, (now, limit)).fetchall()
            if not due:
                return 0
            key = vapid_key(connection)
        accepted = 0
        for job in due:
            code = deliver(json.loads(job["subscription"]), json.loads(job["payload"]), key)
            with database() as connection:
                if code in (404, 410):
                    connection.execute(
                        "DELETE FROM push_subscriptions WHERE session = ? AND subscription = ?",
                        (job["session"], job["subscription"]),
                    )
                elif 200 <= code < 300:
                    accepted += 1
                    connection.execute("""
                        UPDATE push_deliveries SET accepted_at = ? WHERE session = ? AND event = ?
                    """, (now, job["session"], job["event"]))
                else:
                    delay = min(86400, 900 * 2 ** min(job["attempts"], 7))
                    connection.execute("""
                        UPDATE push_deliveries SET attempts = attempts + 1, next_attempt = ?
                        WHERE session = ? AND event = ?
                    """, (now + delay, job["session"], job["event"]))
        return accepted
