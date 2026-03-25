from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import websockets


class HAClientError(RuntimeError):
    pass


@dataclass(slots=True)
class HomeAssistantClient:
    base_url: str
    access_token: str

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def rest_get(self, path: str) -> Any:
        response = requests.get(
            urljoin(self.base_url + "/", path.lstrip("/")),
            headers=self._headers(),
            timeout=30,
        )
        if response.status_code >= 400:
            raise HAClientError(f"GET {path} failed: {response.status_code} {response.text}")
        return response.json()

    def fetch_resource_size(self, resource_url: str) -> int:
        response = requests.get(
            urljoin(self.base_url + "/", resource_url.lstrip("/")),
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30,
        )
        if response.status_code >= 400:
            raise HAClientError(f"GET {resource_url} failed: {response.status_code} {response.text}")
        return len(response.content)

    async def ws_commands(self, commands: list[dict[str, Any]]) -> dict[str, Any]:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_url = f"{scheme}://{parsed.netloc}{parsed.path}/api/websocket"
        results: dict[str, Any] = {}
        async with websockets.connect(ws_url, open_timeout=30, max_size=4 * 1024 * 1024) as websocket:
            auth_required = json.loads(await websocket.recv())
            if auth_required.get("type") != "auth_required":
                raise HAClientError(f"Unexpected WS greeting: {auth_required}")
            await websocket.send(json.dumps({"type": "auth", "access_token": self.access_token}))
            auth_ok = json.loads(await websocket.recv())
            if auth_ok.get("type") != "auth_ok":
                raise HAClientError(f"WS auth failed: {auth_ok}")

            command_keys: dict[int, str] = {}
            for index, command in enumerate(commands, start=1):
                payload = {"id": index, **{k: v for k, v in command.items() if not k.startswith("_")}}
                command_keys[index] = command.get("_key") or command.get("type", f"command_{index}")
                await websocket.send(json.dumps(payload))

            pending = set(command_keys)
            while pending:
                response = json.loads(await websocket.recv())
                response_id = response.get("id")
                if response_id not in pending:
                    continue
                key = command_keys[response_id]
                results[key] = response
                pending.remove(response_id)
        return results

    def run_ws_commands(self, commands: list[dict[str, Any]]) -> dict[str, Any]:
        return asyncio.run(self.ws_commands(commands))
