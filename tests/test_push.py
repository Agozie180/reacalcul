import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import http_ece
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app
import watch_push


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


class PushTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        db = patch.object(app, "DB", Path(temporary.name) / "test.sqlite")
        db.start()
        self.addCleanup(db.stop)
        app.init_db()
        self.now = int(time.time())
        self.receiver = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789ABCDEF"
        public = self.receiver.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self.subscription = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/example",
            "keys": {"auth": encode(self.auth), "p256dh": encode(public)},
        }
        archive = app.archived_records()
        self.july = app.feed_record(archive["25395"])
        self.january = app.feed_record(archive["26199"])

    def save(self, connection, session, serial):
        connection.execute("INSERT INTO checks VALUES (?, 'EFMIS121', ?, 0, ?)", (session, serial, self.now - 1))
        subscription = {**self.subscription, "endpoint": self.subscription["endpoint"] + session}
        watch_push.subscribe(connection, session, watch_push.validate_subscription(subscription))

    def test_actual_range_change_retries_and_records_delivery(self):
        with app.database() as connection:
            self.save(connection, "newly-covered", "A2210")
            self.save(connection, "outside-range", "A2401")
            app.record_feed(connection, [self.july], self.now - 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM push_deliveries").fetchone()[0], 0)
            app.record_feed(connection, [self.july, self.january], self.now)
            app.record_feed(connection, [self.july, self.january], self.now)
            jobs = connection.execute("SELECT session, payload FROM push_deliveries").fetchall()
            self.assertEqual([row["session"] for row in jobs], ["newly-covered"])
            self.assertEqual(json.loads(jobs[0]["payload"])["range"], ["A2001", "A2310"])
        with patch.object(watch_push, "deliver", side_effect=[503, 201]) as sender:
            self.assertEqual(watch_push.dispatch(app.database), 0)
            self.assertEqual(watch_push.dispatch(app.database), 0)
            with app.database() as connection:
                job = connection.execute("SELECT attempts, next_attempt FROM push_deliveries").fetchone()
                self.assertEqual(job["attempts"], 1)
                self.assertGreater(job["next_attempt"], self.now)
                connection.execute("UPDATE push_deliveries SET next_attempt = ?", (self.now,))
            self.assertEqual(watch_push.dispatch(app.database), 1)
            self.assertEqual(watch_push.dispatch(app.database), 0)
            self.assertEqual(sender.call_count, 2)
        with app.database() as connection:
            status = watch_push.status(connection, "newly-covered")
            self.assertEqual(status["last_alert"]["serial"], "A2210")
            watch_push.unsubscribe(connection, "newly-covered")
            self.assertFalse(watch_push.status(connection, "newly-covered")["enabled"])
            self.assertEqual(connection.execute("SELECT count(*) FROM push_deliveries").fetchone()[0], 0)

    def test_same_notice_edit_only_alerts_newly_covered_label(self):
        short_range = {**self.january, "ranges": {"EFMIS121": ["A2001", "A2180"]}, "fingerprint": "old"}
        with app.database() as connection:
            self.save(connection, "already-covered", "A2100")
            self.save(connection, "newly-covered", "A2210")
            app.record_feed(connection, [self.july, short_range], self.now - 1)
            app.record_feed(connection, [self.july, self.january], self.now)
            sessions = connection.execute("SELECT session FROM push_deliveries").fetchall()
            self.assertEqual([row[0] for row in sessions], ["newly-covered"])

    def test_ambiguous_prior_scope_needs_review_instead_of_an_alert(self):
        for prior_models in (["EFMIS121"], []):
            with self.subTest(prior_models=prior_models), app.database() as connection:
                connection.execute("DELETE FROM checks")
                connection.execute("DELETE FROM feed_state")
                connection.execute("DELETE FROM feed_changes")
                self.save(connection, "watching", "A2210")
                prior = {**self.january, "models": prior_models, "ranges": {}, "fingerprint": "ambiguous"}
                app.record_feed(connection, [self.july, prior], self.now - 1)
                app.record_feed(connection, [self.july, self.january], self.now)
                self.assertEqual(connection.execute("SELECT count(*) FROM push_deliveries").fetchone()[0], 0)
                review = app.matching_change(connection, "EFMIS121", "A2210", self.now - 1)
                self.assertIn("confirm previous coverage", review["summary"])
                self.assertEqual(review["source"], self.january["source"])

    def test_web_push_envelope_decrypts(self):
        with app.database() as connection:
            saved_key = watch_push.public_key(connection)
            key = watch_push.vapid_key(connection)
        app.init_db()
        with app.database() as connection:
            self.assertEqual(watch_push.public_key(connection), saved_key)
        payload = watch_push.notification("EFMIS121", "A2210", self.january, example=True)
        with patch.object(requests.Session, "post") as post:
            response = requests.Response()
            response.status_code = 201
            post.return_value = response
            self.assertEqual(watch_push.deliver(self.subscription, payload, key), 201)
            kwargs = post.call_args.kwargs
            self.assertFalse(kwargs["allow_redirects"])
            headers = {name.lower(): value for name, value in kwargs["headers"].items()}
            self.assertIn(f"k={saved_key}", headers["authorization"])
            self.assertEqual(headers["content-encoding"], "aes128gcm")
            decoded = http_ece.decrypt(kwargs["data"], private_key=self.receiver, auth_secret=self.auth)
            self.assertEqual(json.loads(decoded), payload)
