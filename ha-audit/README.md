# ha-audit

`ha-audit` is a command line tool that authenticates against a Home Assistant instance in a local browser, then uses the REST and WebSocket APIs to find likely-unused custom integrations and frontend resources.

## What it audits

- Custom integrations when Home Assistant exposes integration metadata with `is_built_in = false`
- Config entries with little or no observable usage
- Lovelace resources and custom card references
- Custom panels exposed by the frontend API

The output is heuristic. Home Assistant does not expose a single canonical "this integration is unused" API, so the tool correlates config entries, entities, devices, services, loaded components, dashboards, and frontend resources to produce candidates.

## Install

```bash
uv sync
```

## Usage

```bash
uv run ha-audit audit --url http://homeassistant.local:8123
```

Useful options:

```bash
uv run ha-audit audit --url http://homeassistant.local:8123 --force-login
uv run ha-audit audit --url http://homeassistant.local:8123 --format json
uv run ha-audit login --url http://homeassistant.local:8123
uv run ha-audit logout --url http://homeassistant.local:8123
```

## Auth flow

The tool starts a localhost callback server, opens your browser to Home Assistant's authorize page, exchanges the returned code for tokens, and stores the refresh token under `~/.config/ha-audit/auth.json`.
