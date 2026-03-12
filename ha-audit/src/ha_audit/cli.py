from __future__ import annotations

import argparse
import sys

from rich.console import Console

from .auth import AuthError, TokenStore, get_access_token, normalize_base_url
from .audit import render_json_report, render_text_report, run_audit
from .client import HomeAssistantClient


class CLIUsageError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ha-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Authenticate and store a refresh token")
    login_parser.add_argument("url", help="Base URL of the Home Assistant instance")
    login_parser.add_argument("--force-login", action="store_true", help="Force a fresh browser login")

    logout_parser = subparsers.add_parser("logout", help="Delete stored credentials")
    logout_parser.add_argument("url", nargs="?", help="Base URL of the Home Assistant instance")

    audit_parser = subparsers.add_parser("audit", help="Run the audit")
    audit_parser.add_argument("url", nargs="?", help="Base URL of the Home Assistant instance")
    audit_parser.add_argument("--force-login", action="store_true", help="Force a fresh browser login")
    audit_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    return parser


def _choose_saved_url(store: TokenStore, purpose: str) -> str:
    saved_urls = store.list_base_urls()
    if not saved_urls:
        raise CLIUsageError(f"No saved credentials. Pass a URL explicitly: ha-audit {purpose} <url>")
    if len(saved_urls) == 1:
        return saved_urls[0]

    print(f"Multiple saved Home Assistant instances found for '{purpose}':")
    for index, url in enumerate(saved_urls, start=1):
        print(f"{index}. {url}")

    while True:
        choice = input("Choose an instance by number: ").strip()
        if not choice.isdigit():
            print("Enter a number from the list.")
            continue
        selected = int(choice)
        if 1 <= selected <= len(saved_urls):
            return saved_urls[selected - 1]
        print("Enter a valid number from the list.")


def _resolve_url(explicit_url: str | None, purpose: str, store: TokenStore) -> str:
    if explicit_url:
        return normalize_base_url(explicit_url)
    return _choose_saved_url(store, purpose)


def cmd_login(url: str, force_login: bool) -> int:
    access_token = get_access_token(url, force_login=force_login)
    if not access_token:
        raise AuthError("Failed to obtain an access token")
    print(f"Authenticated successfully against {normalize_base_url(url)}")
    return 0


def cmd_logout(url: str | None) -> int:
    store = TokenStore()
    normalized = _resolve_url(url, "logout", store)
    if store.delete(normalized):
        print(f"Removed stored credentials for {normalized}")
    else:
        print(f"No stored credentials for {normalized}")
    return 0


def cmd_audit(url: str | None, force_login: bool, output_format: str) -> int:
    console = Console()
    store = TokenStore()
    normalized = _resolve_url(url, "audit", store)
    access_token = get_access_token(normalized, force_login=force_login, store=store)
    client = HomeAssistantClient(base_url=normalized, access_token=access_token)
    report = run_audit(client)
    if output_format == "json":
        print(render_json_report(report))
    else:
        console.print(render_text_report(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            return cmd_login(args.url, args.force_login)
        if args.command == "logout":
            return cmd_logout(args.url)
        if args.command == "audit":
            return cmd_audit(args.url, args.force_login, args.format)
    except CLIUsageError as exc:
        print(f"Usage error: {exc}", file=sys.stderr)
        return 2
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
