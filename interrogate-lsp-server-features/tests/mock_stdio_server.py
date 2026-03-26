from __future__ import annotations

import json
import sys


def read_message() -> dict:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"\r\n":
            break
        name, _, value = line.decode("ascii").partition(":")
        headers[name.lower().strip()] = value.strip()
    size = int(headers["content-length"])
    payload = sys.stdin.buffer.read(size)
    return json.loads(payload.decode("utf-8"))


def write_message(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message.get("method") == "initialize":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "serverInfo": {"name": "Mock Stdio", "version": "1.0"},
                    "capabilities": {
                        "hoverProvider": True,
                        "definitionProvider": True,
                        "completionProvider": {"triggerCharacters": [".", ":"]},
                        "textDocumentSync": {"openClose": True, "change": 2, "save": {"includeText": False}},
                        "workspace": {"workspaceFolders": {"supported": True}},
                    },
                },
            }
        )
    elif message.get("method") == "shutdown":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif message.get("method") == "exit":
        raise SystemExit(0)
