"""tests/sandbox/mcp_probe_server.py: the stdio MCP server the VM's connectors part starts, checked
here with a real round trip, so a mistake in it shows outside the VM too."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SERVER = Path(__file__).resolve().parents[2] / "tests" / "sandbox" / "mcp_probe_server.py"


def round_trip(messages: list[Any], *args: str) -> list[dict[str, Any]]:
    """Each message a line on the server's stdin; its replies, a JSON object a line."""
    text = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    done = subprocess.run(
        [sys.executable, str(SERVER), *args],
        input=text,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return [json.loads(line) for line in done.stdout.splitlines()]


def request(id_: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


INITIALIZE = request(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}})
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class ProbeServerTest(unittest.TestCase):
    def test_a_session_from_initialize_to_a_call(self) -> None:
        replies = round_trip(
            [
                INITIALIZE,
                INITIALIZED,
                request(2, "tools/list"),
                request(3, "tools/call", {"name": "probe_allowed"}),
            ],
            "allowed",
        )
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])  # nothing for the notification
        init = replies[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-03-26")
        self.assertEqual(init["capabilities"], {"tools": {}})
        self.assertIn("name", init["serverInfo"])
        (tool,) = replies[1]["result"]["tools"]
        self.assertEqual(tool["name"], "probe_allowed")
        self.assertEqual(tool["inputSchema"], {"type": "object", "properties": {}})
        self.assertEqual(replies[2]["result"]["content"][0]["type"], "text")
        self.assertTrue(all(r["jsonrpc"] == "2.0" for r in replies))

    def test_anything_else_is_an_error(self) -> None:
        (answer,) = round_trip([request(7, "resources/list")], "x")
        self.assertEqual(answer["id"], 7)
        self.assertEqual(answer["error"]["code"], -32601)
        self.assertNotIn("result", answer)

    def test_a_line_that_is_not_json_is_a_parse_error_and_it_goes_on(self) -> None:
        replies = round_trip(["{not json", request(2, "tools/list")], "x")
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["result"]["tools"][0]["name"], "probe_x")

    def test_it_logs_what_it_was_asked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "probe.log"
            round_trip([INITIALIZE, INITIALIZED, request(2, "tools/list")], "blocked", str(log))
            self.assertEqual(
                log.read_text().splitlines(),
                ["blocked initialize", "blocked notifications/initialized", "blocked tools/list"],
            )

    def test_without_a_word_it_says_how_to_run_it(self) -> None:
        done = subprocess.run(
            [sys.executable, str(SERVER)], capture_output=True, text=True, timeout=10, check=False
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("Usage", done.stderr)


if __name__ == "__main__":
    unittest.main()
