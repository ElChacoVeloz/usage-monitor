import importlib.util
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from urllib.error import HTTPError

SPEC = importlib.util.spec_from_file_location(
    "usage_monitor", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ClaudeRateLimitTest(unittest.TestCase):
    def test_rate_limited_connected_account_is_not_reported_as_unconfigured(self):
        cfg = server.load_config()
        now = datetime.now(ZoneInfo("America/Chicago"))
        server.NEG.clear()
        server.CACHE.bust()
        with patch.object(server, "OFFLINE", False), patch.object(
            server, "claude_usage", side_effect=HTTPError(
                "https://api.anthropic.com/api/oauth/usage", 429,
                "Too Many Requests", {"Retry-After": "0"}, None)):
            card = server.source_claude(cfg, now, False, measured={}, manual=None)
        self.assertEqual(card["source_mode"], "error")
        self.assertEqual(card["source_label"], "CUOTA EN PAUSA")
        self.assertIn("429", card["hint"])
        self.assertNotIn("instala Claude Code", card.get("hint", ""))


if __name__ == "__main__":
    unittest.main()
