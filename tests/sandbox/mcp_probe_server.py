#!/usr/bin/env python3
"""A stdio MCP server that does nothing but say which server it is, for tests/sandbox/live.sh's
connectors part: a window's agent shows its tool, `probe_<word>`, only when the window may reach it.

Usage: mcp_probe_server.py <word> [<log file>]

Newline-delimited JSON-RPC 2.0 on stdin and stdout, plain Python, no packages. It answers
`initialize` (the client's protocol version echoed), `tools/list` (one tool, `probe_<word>`) and
`tools/call` (a line of text), takes `notifications/initialized` and any other notification
without a reply, and answers anything else with an error. With <log file>, each message it reads is
appended there as `<word> <method>`: which servers an agent started, and what it asked them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO


SERVER = {"name": "bosun-probe", "version": "1.0.0"}
DEFAULT_PROTOCOL = "2025-06-18"
METHOD_NOT_FOUND = -32601
PARSE_ERROR = -32700


def tool_name(word: str) -> str:
    return f"probe_{word}"


def result_for(word: str, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """The result of a request, or None for a method this server does not have."""
    if method == "initialize":
        version = params.get("protocolVersion")
        return {
            "protocolVersion": version if isinstance(version, str) else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER,
        }
    if method == "tools/list":
        tool = {
            "name": tool_name(word),
            "description": f"Says that the probe server {word} answered.",
            "inputSchema": {"type": "object", "properties": {}},
        }
        return {"tools": [tool]}
    if method == "tools/call":
        return {"content": [{"type": "text", "text": f"the probe server {word} answered"}]}
    return None


def reply(word: str, message: Any) -> dict[str, Any] | None:
    """The answer to one message: None for a notification (no id), which gets none."""
    if not isinstance(message, dict) or "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params")
    result = (
        result_for(word, method, params if isinstance(params, dict) else {})
        if isinstance(method, str)
        else None
    )
    if result is None:
        error = {"code": METHOD_NOT_FOUND, "message": f"no method {method!r}"}
        return {"jsonrpc": "2.0", "id": message["id"], "error": error}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def log(path: Path | None, word: str, message: Any) -> None:
    if path is None:
        return
    method = message.get("method") if isinstance(message, dict) else None
    with path.open("a") as out:
        out.write(f"{word} {method or '-'}\n")


def serve(word: str, log_path: Path | None, lines: TextIO, out: TextIO) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            answer: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": "not JSON"},
            }
        else:
            log(log_path, word, message)
            answer = reply(word, message)
        if answer is not None:
            out.write(json.dumps(answer) + "\n")
            out.flush()


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2) or not argv[0]:
        print(__doc__, file=sys.stderr)
        return 2
    serve(argv[0], Path(argv[1]) if len(argv) == 2 else None, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
