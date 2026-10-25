import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "usage_monitor_claude_refresh", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class ClaudeRefreshTest(unittest.TestCase):
    def test_expired_token_refresh_uses_current_platform_json_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / "credentials.json"
            creds.write_text(json.dumps({"claudeAiOauth": {
                "accessToken": "expired-access",
                "refreshToken": "valid-refresh",
                "expiresAt": 1,
                "scopes": ["user:inference"],
            }}), encoding="utf-8")
            captured = {}

            def fake_http_json(url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return {"access_token": "fresh-access",
                        "refresh_token": "rotated-refresh", "expires_in": 3600}

            with patch.object(server, "CLAUDE_CREDS", str(creds)), \
                    patch.object(server, "http_json", side_effect=fake_http_json), \
                    patch.object(server.time, "time", return_value=2_000_000_000):
                token = server.claude_access()

            self.assertEqual(token, "fresh-access")
            self.assertEqual(captured["url"],
                             "https://platform.claude.com/v1/oauth/token")
            self.assertEqual(captured["method"], "POST")
            self.assertEqual(captured["headers"]["Content-Type"], "application/json")
            self.assertTrue(captured["headers"]["User-Agent"].startswith("claude-cli/"))
            self.assertEqual(json.loads(captured["body"]), {
                "grant_type": "refresh_token",
                "refresh_token": "valid-refresh",
                "client_id": server.CLAUDE_CLIENT_ID,
            })
            saved = json.loads(creds.read_text(encoding="utf-8"))["claudeAiOauth"]
            self.assertEqual(saved["refreshToken"], "rotated-refresh")
            self.assertEqual(saved["scopes"], ["user:inference"])
            self.assertEqual(os.stat(creds).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
