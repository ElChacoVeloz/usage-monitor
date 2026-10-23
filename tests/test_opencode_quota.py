import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

SPEC = importlib.util.spec_from_file_location(
    "usage_monitor", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)

PAYLOAD = {"usage": {
    "rolling": {"percent": 5, "resetsAt": "2026-10-23T03:22:12.000Z"},
    "weekly": {"percent": 14, "resetsAt": "2026-10-26T00:00:00.000Z"},
    "monthly": {"percent": 8, "resetsAt": "2026-11-18T14:59:57.000Z"},
}}


class OpenCodeUsageTest(unittest.TestCase):
    def test_reports_the_percentages_the_website_shows(self):
        cfg = server.load_config()
        now = datetime.now(ZoneInfo("America/Chicago"))
        server.NEG.clear()
        server.CACHE.bust()
        with patch.object(server, "OFFLINE", False), patch.object(
                server, "opencode_usage", return_value=PAYLOAD):
            card = server.source_opencode(cfg, now, False)
        pct = {w["id"]: w["pct"] for w in card["windows"]}
        self.assertEqual(pct, {"session": 5.0, "week": 14.0, "month": 8.0})
        self.assertEqual(card["source_mode"], "live")
        self.assertEqual(card["source_label"], "EN VIVO")
        resets = {w["id"]: w["reset_at"] for w in card["windows"]}
        self.assertEqual(datetime.fromtimestamp(resets["session"], ZoneInfo("UTC")).year, 2026)
        self.assertAlmostEqual(resets["week"], 1792972800.0, delta=1)

    def test_falls_back_to_measured_when_the_api_fails(self):
        cfg = server.load_config()
        now = datetime.now(ZoneInfo("America/Chicago"))
        server.NEG.clear()
        server.CACHE.bust()
        measured = {"session": {"tokens": 1200, "calls": 4},
                    "week": {"tokens": 9000, "calls": 30},
                    "month": {"tokens": 41000, "calls": 120}}
        with patch.object(server, "OFFLINE", False), patch.object(
                server, "opencode_usage", side_effect=RuntimeError("HTTP Error 403")):
            card = server.source_opencode(cfg, now, False, measured)
        self.assertEqual(card["source_mode"], "measured")
        self.assertTrue(all(w["kind"] == "stat" for w in card["windows"]))
        self.assertIn("403", card["source_note"] + card.get("hint", ""))

    def test_no_quota_and_no_usage_says_why_not_just_zero(self):
        cfg = server.load_config()
        now = datetime.now(ZoneInfo("America/Chicago"))
        server.NEG.clear()
        server.CACHE.bust()
        with patch.object(server, "OFFLINE", False), patch.object(
                server, "opencode_usage", side_effect=RuntimeError("HTTP Error 403")):
            card = server.source_opencode(cfg, now, False, {})
        self.assertEqual(card["source_mode"], "error")
        self.assertIn("403", card["hint"])


if __name__ == "__main__":
    unittest.main()
