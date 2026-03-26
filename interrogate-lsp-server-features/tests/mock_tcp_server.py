from __future__ import annotations

import json
import socket
import sys


def read_message(stream) -> dict:
    headers = {}
    while True:
        line = stream.readline()
        if line == b"\r\n":
            break
        name, _, value = line.decode("ascii").partition(":")
        headers[name.lower().strip()] = value.strip()
    size = int(headers["content-length"])
    payload = stream.read(size)
    return json.loads(payload.decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


host = "127.0.0.1"
port = int(sys.argv[1])
with socket.create_server((host, port), reuse_port=False) as server:
    conn, _ = server.accept()
    with conn, conn.makefile("rb") as reader, conn.makefile("wb") as writer:
        while True:
            message = read_message(reader)
            if message.get("method") == "initialize":
                write_message(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "serverInfo": {"name": "Mock TCP", "version": "2.0"},
                            "capabilities": {
                                "hoverProvider": True,
                                "renameProvider": {"prepareProvider": True},
                                "semanticTokensProvider": {
                                    "legend": {"tokenTypes": ["class"], "tokenModifiers": ["declaration"]},
                                    "full": True,
                                },
                                "textDocumentSync": 1,
                            },
                        },
                    },
                )
            elif message.get("method") == "shutdown":
                write_message(writer, {"jsonrpc": "2.0", "id": message["id"], "result": None})
            elif message.get("method") == "exit":
                raise SystemExit(0)
