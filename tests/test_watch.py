import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class WatchTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        override = patch.object(app, "DB", Path(temporary.name) / "test.sqlite")
        override.start()
        self.addCleanup(override.stop)
        app.init_db()
        raw = (app.ROOT / "cpsc-notices.json").read_text(encoding="utf-8")
        self.source = next(record for record in json.loads(raw) if app.id_for(record) == "26199")

    def test_same_recall_number_change(self):
        earlier = dict(self.source, Description=self.source["Description"].replace("A2310", "A2180"))
        with app.database() as connection:
            app.record_feed(connection, [app.feed_record(earlier)], 100)
            self.assertIsNone(app.matching_change(connection, "EFMIS121", "A2210", 95))
            app.record_feed(connection, [app.feed_record(self.source)], 200)
            app.record_feed(connection, [app.feed_record(self.source)], 300)
            count = connection.execute("SELECT COUNT(*) FROM feed_changes").fetchone()[0]
            self.assertEqual(count, 1)
            change = app.matching_change(connection, "EFMIS121", "A2210", 95)
            self.assertEqual(change["before_range"], ["A2001", "A2180"])
            self.assertEqual(change["after_range"], ["A2001", "A2310"])
            self.assertIn("newly listed range", change["summary"])
            self.assertIn("remains", app.matching_change(connection, "EFMIS121", "A2100", 95)["summary"])
            self.assertIsNone(app.matching_change(connection, "EFMIS175", "A2210", 95))
            self.assertIsNone(app.matching_change(connection, "EFMIS121", "A2210", 210))

    def test_new_notice_and_whitespace_normalization(self):
        changed = dict(self.source, RecallNumber="26999", Description=self.source["Description"].replace(" Between ", "  Between   "))
        first = app.feed_record(self.source)
        added = app.feed_record(changed)
        self.assertEqual(first["fingerprint"], added["fingerprint"])
        with app.database() as connection:
            app.record_feed(connection, [first], 100)
            app.record_feed(connection, [first, added], 200)
            self.assertIsNotNone(app.matching_change(connection, "EFMIS121", "A2210", 99))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM feed_changes").fetchone()[0], 1)
