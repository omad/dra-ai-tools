# ha-audit

`ha-audit` is a command line tool that authenticates against a Home Assistant instance in a local browser, then uses the REST and WebSocket APIs to find likely-unused custom integrations and frontend resources.

## What it audits

- Custom integrations from Home Assistant when `is_built_in = false` is exposed
- Custom integrations installed through HACS via HACS websocket commands
- Config entries with little or no observable usage
- Lovelace resources and custom card references
- Custom panels exposed by the frontend API

The output is heuristic. Home Assistant does not expose a single canonical "this integration is unused" API, so the tool correlates config entries, entities, devices, services, dashboards, frontend resources, and HACS repository metadata to produce candidates.

## Install

```bash
uv sync
```

## Usage

Authenticate and save credentials:

```bash
uv run ha-audit login http://homeassistant.local:8123
```

Run an audit:

```bash
uv run ha-audit audit
uv run ha-audit audit http://homeassistant.local:8123
uv run ha-audit audit --format json
```

Behavior when choosing an instance:

- If no credentials are saved, `audit` requires a URL.
- If one credential set is saved, `audit` uses it automatically.
- If two or more are saved, `audit` prompts you to choose one.

Other commands:

```bash
uv run ha-audit login http://homeassistant.local:8123 --force-login
uv run ha-audit logout
uv run ha-audit logout http://homeassistant.local:8123
```

## Auth flow

The tool starts a localhost callback server, opens your browser to Home Assistant's authorize page, exchanges the returned code for tokens, and stores the refresh token under `~/.config/ha-audit/auth.json`.
