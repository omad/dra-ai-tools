from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .client import HomeAssistantClient


@dataclass(slots=True)
class AuditReport:
    base_url: str
    summary: dict[str, Any]
    custom_integrations: list[dict[str, Any]]
    frontend_resources: list[dict[str, Any]]
    custom_panels: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "summary": self.summary,
            "custom_integrations": self.custom_integrations,
            "frontend_resources": self.frontend_resources,
            "custom_panels": self.custom_panels,
            "warnings": self.warnings,
        }


def _safe_rest_get(client: HomeAssistantClient, path: str, warnings: list[str]) -> Any | None:
    try:
        return client.rest_get(path)
    except Exception as exc:
        warnings.append(f"REST {path} unavailable: {exc}")
        return None


def _try_rest_get(client: HomeAssistantClient, path: str) -> tuple[Any | None, str | None]:
    try:
        return client.rest_get(path), None
    except Exception as exc:
        return None, f"REST {path} unavailable: {exc}"


def _safe_ws(client: HomeAssistantClient, commands: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    try:
        return client.run_ws_commands(commands)
    except Exception as exc:
        warnings.append(f"WS batch unavailable: {exc}")
        return {}


def _flatten_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_flatten_strings(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_flatten_strings(item))
    return found


def _extract_custom_card_types(value: Any) -> list[str]:
    return sorted(
        {
            text.split(":", 1)[1]
            for text in _flatten_strings(value)
            if isinstance(text, str) and text.startswith("custom:") and len(text.split(":", 1)) == 2
        }
    )


def _count_domain_references(value: Any, domains: set[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for text in _flatten_strings(value):
        if "." not in text:
            continue
        domain = text.split(".", 1)[0]
        if domain in domains:
            counts[domain] += 1
    return counts


def _merge_dashboard_payloads(configs: list[Any]) -> Any:
    if len(configs) == 1:
        return configs[0]
    return {"dashboards": configs}


def _guess_resource_tags(url: str) -> list[str]:
    tokens = set()
    for match in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", url.lower()):
        tokens.add(match)
        if match.endswith("-bundle"):
            tokens.add(match.removesuffix("-bundle"))
        if match.endswith("-card"):
            tokens.add(match)
    return sorted(tokens)


def _guess_hacs_plugin_matches(url: str, hacs_plugins: list[dict[str, Any]]) -> list[str]:
    lowered = url.lower()
    matches = []
    for plugin in hacs_plugins:
        candidates = {
            str(plugin.get("name") or "").lower(),
            str(plugin.get("domain") or "").lower(),
            str(plugin.get("file_name") or "").lower(),
            str(plugin.get("full_name") or "").lower().split("/")[-1],
        }
        candidates = {item for item in candidates if item}
        if any(candidate in lowered for candidate in candidates):
            matches.append(plugin.get("full_name") or plugin.get("name") or plugin.get("id"))
    return sorted(set(matches))


def run_audit(client: HomeAssistantClient) -> AuditReport:
    warnings: list[str] = []

    rest_config = _safe_rest_get(client, "/api/config", warnings) or {}
    components = _safe_rest_get(client, "/api/components", warnings) or []
    states = _safe_rest_get(client, "/api/states", warnings) or []
    services = _safe_rest_get(client, "/api/services", warnings) or []
    integrations_catalog, integrations_warning = _try_rest_get(client, "/api/config/integrations")
    lovelace_resources, lovelace_resources_warning = _try_rest_get(client, "/api/lovelace/resources")
    lovelace_config, lovelace_config_warning = _try_rest_get(client, "/api/lovelace/config")

    ws_results = _safe_ws(
        client,
        [
            {"type": "config/entity_registry/list"},
            {"type": "config/device_registry/list"},
            {"type": "config_entries/get"},
            {"type": "frontend/get_panels"},
            {"type": "lovelace/resources"},
            {"type": "lovelace/config", "url_path": None, "_key": "lovelace/config_default"},
            {"type": "lovelace/dashboards/list"},
            {"type": "hacs/repositories/list", "_key": "hacs/repositories/list"},
        ],
        warnings,
    )

    entity_registry = ws_results.get("config/entity_registry/list", {}).get("result") or []
    device_registry = ws_results.get("config/device_registry/list", {}).get("result") or []
    config_entries = ws_results.get("config_entries/get", {}).get("result") or []
    panels = ws_results.get("frontend/get_panels", {}).get("result") or {}
    hacs_repositories = ws_results.get("hacs/repositories/list", {}).get("result") or []
    if lovelace_resources is None:
        lovelace_resources = ws_results.get("lovelace/resources", {}).get("result") or []
    if lovelace_resources is None and lovelace_resources_warning:
        warnings.append(lovelace_resources_warning)
    if lovelace_config is None:
        lovelace_config = ws_results.get("lovelace/config_default", {}).get("result") or {}
    if lovelace_config is None and lovelace_config_warning:
        warnings.append(lovelace_config_warning)

    dashboards = ws_results.get("lovelace/dashboards/list", {}).get("result") or []
    if dashboards:
        dashboard_commands = []
        for dashboard in dashboards:
            url_path = dashboard.get("url_path")
            key = f"dashboard::{url_path or '__default__'}"
            dashboard_commands.append({"type": "lovelace/config", "url_path": url_path, "_key": key})
        dashboard_results = _safe_ws(client, dashboard_commands, warnings)
        merged_dashboards = []
        for dashboard in dashboards:
            url_path = dashboard.get("url_path")
            key = f"dashboard::{url_path or '__default__'}"
            payload = dashboard_results.get(key, {}).get("result")
            if payload is None:
                payload = lovelace_config if url_path in (None, "") else None
            if payload is not None:
                merged_dashboards.append(
                    {
                        "title": dashboard.get("title"),
                        "url_path": url_path,
                        "require_admin": dashboard.get("require_admin"),
                        "config": payload,
                    }
                )
        if merged_dashboards:
            lovelace_config = _merge_dashboard_payloads(merged_dashboards)

    hacs_ws_response = ws_results.get("hacs/repositories/list")
    if integrations_catalog is None and not hacs_repositories and integrations_warning:
        warnings.append(integrations_warning)
    if "hacs" in components and hacs_ws_response is None:
        warnings.append("WS hacs/repositories/list unavailable even though HACS appears to be loaded")
    if "hacs" in components and hacs_ws_response and not hacs_ws_response.get("success", True):
        warnings.append(f"HACS websocket lookup failed: {hacs_ws_response.get('error')}")

    state_domains = Counter(item["entity_id"].split(".", 1)[0] for item in states if "entity_id" in item)
    service_domains = Counter(item["domain"] for item in services if "domain" in item)
    registry_domains = Counter(item["entity_id"].split(".", 1)[0] for item in entity_registry if "entity_id" in item)

    integrations_by_domain = {}
    if isinstance(integrations_catalog, list):
        integrations_by_domain = {item.get("domain"): item for item in integrations_catalog if item.get("domain")}
    elif isinstance(integrations_catalog, dict):
        integrations_by_domain = integrations_catalog

    hacs_integrations = [
        repo for repo in hacs_repositories if repo.get("installed") and repo.get("category") == "integration"
    ]
    hacs_plugins = [repo for repo in hacs_repositories if repo.get("installed") and repo.get("category") == "plugin"]
    hacs_integrations_by_domain = {
        repo.get("domain"): repo for repo in hacs_integrations if repo.get("domain")
    }

    dashboard_custom_cards = _extract_custom_card_types(lovelace_config)
    dashboard_domain_refs = _count_domain_references(lovelace_config, set(state_domains) | set(registry_domains))

    entry_devices = defaultdict(int)
    for device in device_registry:
        for entry_id in device.get("config_entries", []):
            entry_devices[entry_id] += 1

    custom_integrations: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for entry in config_entries:
        domain = entry.get("domain")
        entry_id = entry.get("entry_id")
        catalog = integrations_by_domain.get(domain, {})
        hacs_repo = hacs_integrations_by_domain.get(domain, {})
        is_custom = catalog.get("is_built_in") is False or bool(hacs_repo)
        if not is_custom:
            continue

        entities = registry_domains.get(domain, 0)
        live_entities = state_domains.get(domain, 0)
        services_count = service_domains.get(domain, 0)
        dashboard_refs = dashboard_domain_refs.get(domain, 0)
        devices = entry_devices.get(entry_id, 0)
        loaded = domain in components
        score = entities + live_entities + services_count + dashboard_refs + devices + int(loaded)

        reasons = []
        if not loaded:
            reasons.append("component not loaded")
        if entities == 0 and live_entities == 0:
            reasons.append("no entities found")
        if devices == 0:
            reasons.append("no devices linked")
        if services_count == 0:
            reasons.append("no services exposed")
        if dashboard_refs == 0:
            reasons.append("not referenced in Lovelace config")

        custom_integrations.append(
            {
                "domain": domain,
                "title": entry.get("title") or catalog.get("name") or hacs_repo.get("name") or domain,
                "entry_id": entry_id,
                "source": "hacs" if hacs_repo else "home_assistant",
                "repository": hacs_repo.get("full_name"),
                "loaded": loaded,
                "entities": entities,
                "live_entities": live_entities,
                "devices": devices,
                "services": services_count,
                "dashboard_references": dashboard_refs,
                "usage_score": score,
                "candidate_unused": score <= 1,
                "reasons": reasons,
            }
        )
        seen_domains.add(domain)

    for domain, hacs_repo in hacs_integrations_by_domain.items():
        if domain in seen_domains:
            continue
        entities = registry_domains.get(domain, 0)
        live_entities = state_domains.get(domain, 0)
        services_count = service_domains.get(domain, 0)
        dashboard_refs = dashboard_domain_refs.get(domain, 0)
        loaded = domain in components
        score = entities + live_entities + services_count + dashboard_refs + int(loaded)

        reasons = []
        if not loaded:
            reasons.append("component not loaded")
        if entities == 0 and live_entities == 0:
            reasons.append("no entities found")
        if services_count == 0:
            reasons.append("no services exposed")
        if dashboard_refs == 0:
            reasons.append("not referenced in Lovelace config")
        reasons.append("installed in HACS but no config entry found")

        custom_integrations.append(
            {
                "domain": domain,
                "title": hacs_repo.get("name") or domain,
                "entry_id": None,
                "source": "hacs",
                "repository": hacs_repo.get("full_name"),
                "loaded": loaded,
                "entities": entities,
                "live_entities": live_entities,
                "devices": 0,
                "services": services_count,
                "dashboard_references": dashboard_refs,
                "usage_score": score,
                "candidate_unused": score <= 1,
                "reasons": reasons,
            }
        )

    custom_integrations.sort(key=lambda item: (not item["candidate_unused"], item["usage_score"], item["domain"]))

    frontend_resources_report: list[dict[str, Any]] = []
    if isinstance(lovelace_resources, list):
        for resource in lovelace_resources:
            url = resource.get("url") or resource.get("id") or ""
            guessed_tags = _guess_resource_tags(url)
            matched_tags = sorted(set(guessed_tags) & set(dashboard_custom_cards))
            matched_hacs_plugins = _guess_hacs_plugin_matches(url, hacs_plugins)
            frontend_resources_report.append(
                {
                    "url": url,
                    "resource_type": resource.get("type"),
                    "guessed_tags": guessed_tags,
                    "matched_custom_cards": matched_tags,
                    "matched_hacs_plugins": matched_hacs_plugins,
                    "candidate_unused": len(matched_tags) == 0 and bool(dashboard_custom_cards),
                }
            )
    frontend_resources_report.sort(key=lambda item: (not item["candidate_unused"], item["url"]))

    custom_panels = []
    if isinstance(panels, dict):
        for panel_key, panel in panels.items():
            component_name = panel.get("component_name")
            if component_name and not component_name.startswith("ha-"):
                custom_panels.append(
                    {
                        "panel_key": panel_key,
                        "component_name": component_name,
                        "icon": panel.get("icon"),
                        "require_admin": panel.get("require_admin"),
                    }
                )
    custom_panels.sort(key=lambda item: item["panel_key"])

    summary = {
        "instance_name": rest_config.get("location_name"),
        "version": rest_config.get("version"),
        "loaded_components": len(components),
        "states": len(states),
        "config_entries": len(config_entries),
        "custom_integrations": len(custom_integrations),
        "candidate_unused_custom_integrations": sum(1 for item in custom_integrations if item["candidate_unused"]),
        "hacs_installed_integrations": len(hacs_integrations),
        "hacs_installed_plugins": len(hacs_plugins),
        "frontend_resources": len(frontend_resources_report),
        "candidate_unused_frontend_resources": sum(1 for item in frontend_resources_report if item["candidate_unused"]),
        "custom_cards_detected": dashboard_custom_cards,
        "custom_panels": len(custom_panels),
    }

    return AuditReport(
        base_url=client.base_url,
        summary=summary,
        custom_integrations=custom_integrations,
        frontend_resources=frontend_resources_report,
        custom_panels=custom_panels,
        warnings=warnings,
    )


def render_text_report(report: AuditReport) -> str:
    lines = []
    lines.append(f"Home Assistant audit for {report.base_url}")
    if report.summary.get("instance_name"):
        lines.append(f"Instance: {report.summary['instance_name']}")
    if report.summary.get("version"):
        lines.append(f"Version: {report.summary['version']}")
    lines.append("")
    lines.append("Summary")
    for key, value in report.summary.items():
        if key in {"instance_name", "version"}:
            continue
        lines.append(f"- {key}: {value}")

    lines.append("")
    lines.append("Custom integration candidates")
    if report.custom_integrations:
        for item in report.custom_integrations:
            prefix = "[candidate]" if item["candidate_unused"] else "[in use]"
            lines.append(
                f"- {prefix} {item['domain']} ({item['title']}): "
                f"score={item['usage_score']}, entities={item['entities']}, live_entities={item['live_entities']}, "
                f"devices={item['devices']}, services={item['services']}, dashboard_refs={item['dashboard_references']}"
            )
            if item.get("repository"):
                lines.append(f"  repository: {item['repository']}")
            if item["reasons"]:
                lines.append(f"  reasons: {', '.join(item['reasons'])}")
    else:
        lines.append("- No custom integrations could be identified from the available APIs.")

    lines.append("")
    lines.append("Frontend resource candidates")
    if report.frontend_resources:
        for item in report.frontend_resources:
            prefix = "[candidate]" if item["candidate_unused"] else "[in use]"
            lines.append(
                f"- {prefix} {item['url']} type={item['resource_type']} matched_cards={item['matched_custom_cards']}"
            )
            if item.get("matched_hacs_plugins"):
                lines.append(f"  HACS plugins: {item['matched_hacs_plugins']}")
    else:
        lines.append("- No Lovelace resources found.")

    lines.append("")
    lines.append("Custom panels")
    if report.custom_panels:
        for item in report.custom_panels:
            lines.append(
                f"- {item['panel_key']}: component={item['component_name']} require_admin={item['require_admin']}"
            )
    else:
        lines.append("- No custom panels found.")

    if report.warnings:
        lines.append("")
        lines.append("Warnings")
        for warning in report.warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def render_json_report(report: AuditReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)
