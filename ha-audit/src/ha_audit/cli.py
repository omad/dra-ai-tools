from __future__ import annotations

import argparse
import sys

from .auth import AuthError, TokenStore, get_access_token, normalize_base_url
from .audit import render_json_report, render_text_report, run_audit
from .client import HomeAssistantClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ha-audit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--url", required=True, help="Base URL of the Home Assistant instance")

    login_parser = subparsers.add_parser("login", parents=[shared], help="Authenticate and store a refresh token")
    login_parser.add_argument("--force-login", action="store_true", help="Force a fresh browser login")

    logout_parser = subparsers.add_parser("logout", parents=[shared], help="Delete the stored refresh token")

    audit_parser = subparsers.add_parser("audit", parents=[shared], help="Run the audit")
    audit_parser.add_argument("--force-login", action="store_true", help="Force a fresh browser login")
    audit_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    return parser


def cmd_login(url: str, force_login: bool) -> int:
    access_token = get_access_token(url, force_login=force_login)
    if not access_token:
        raise AuthError("Failed to obtain an access token")
    print(f"Authenticated successfully against {normalize_base_url(url)}")
    return 0


def cmd_logout(url: str) -> int:
    store = TokenStore()
    normalized = normalize_base_url(url)
    if store.delete(normalized):
        print(f"Removed stored credentials for {normalized}")
    else:
        print(f"No stored credentials for {normalized}")
    return 0


def cmd_audit(url: str, force_login: bool, output_format: str) -> int:
    normalized = normalize_base_url(url)
    access_token = get_access_token(normalized, force_login=force_login)
    client = HomeAssistantClient(base_url=normalized, access_token=access_token)
    report = run_audit(client)
    if output_format == "json":
        print(render_json_report(report))
    else:
        print(render_text_report(report))
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
    except AuthError as exc:
        print(f"Authentication error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
