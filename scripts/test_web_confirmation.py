"""Verify that the Web GUI returns confirmation prompts as HTTP responses."""

from http.server import HTTPServer
import json
from pathlib import Path
import sys
import threading
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import web_ember


def test_confirmation_response():
    token = "test-token"
    context = web_ember.EmberWebContext(token)
    server = HTTPServer(("127.0.0.1", 0), web_ember.EmberHTTPHandler)
    server.ctx = context
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"query": "delete file X"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json", "X-Ember-Token": token},
        )
        fake_result = {
            "route": "confirmation_required",
            "tool": "delete_file",
            "response": "WARNING: permanently delete 'X'? (yes/no)",
            "display_response": "WARNING: permanently delete 'X'? (yes/no)",
        }
        with patch.object(web_ember, "handle_request", return_value=fake_result):
            with urllib.request.urlopen(request) as response:
                assert response.status == 200
                body = json.loads(response.read().decode("utf-8"))
        assert body["answer"] == fake_result["display_response"]
        assert body["meta"]["route"] == "confirmation_required"
        assert body["meta"]["tool"] == "delete_file"
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_confirmation_response()
    print("PASS: Web confirmation response")

