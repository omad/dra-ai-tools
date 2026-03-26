from __future__ import annotations

import argparse
import collections
import html
import json
import os
import socket
import subprocess
import sys
import threading
import textwrap
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REGISTRY_PATH = Path("lsp-capabilities.json")

CAPABILITY_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Language Features",
        [
            ("hoverProvider", "Supports hover information at a symbol or cursor location."),
            ("definitionProvider", "Supports go-to-definition requests."),
            ("declarationProvider", "Supports go-to-declaration requests."),
            ("typeDefinitionProvider", "Supports go-to-type-definition requests."),
            ("implementationProvider", "Supports go-to-implementation requests."),
            ("referencesProvider", "Supports finding symbol references."),
            ("documentHighlightProvider", "Highlights related symbols within a document."),
            ("documentSymbolProvider", "Lists symbols within a single document."),
            ("workspaceSymbolProvider", "Searches symbols across the workspace."),
            ("renameProvider", "Supports symbol rename operations."),
            ("semanticTokensProvider", "Provides semantic token classification for syntax-aware highlighting."),
            ("linkedEditingRangeProvider", "Supports linked editing for mirrored ranges."),
            ("monikerProvider", "Provides cross-tool symbol identities."),
            ("typeHierarchyProvider", "Supports type hierarchy exploration."),
            ("callHierarchyProvider", "Supports call hierarchy exploration."),
            ("inlineValueProvider", "Provides inline values, often for debugging."),
            ("inlayHintProvider", "Provides inlay hints such as inferred types or parameter names."),
            ("diagnosticProvider", "Supports pull-based diagnostics."),
        ],
    ),
    (
        "Editing & Actions",
        [
            ("completionProvider", "Offers code completion candidates."),
            ("signatureHelpProvider", "Shows callable signatures and parameter help."),
            ("codeActionProvider", "Provides code actions like quick fixes or refactors."),
            ("codeLensProvider", "Provides code lenses embedded in the editor."),
            ("documentFormattingProvider", "Formats an entire document."),
            ("documentRangeFormattingProvider", "Formats a selected range."),
            ("documentOnTypeFormattingProvider", "Formats while typing trigger characters."),
            ("foldingRangeProvider", "Provides folding ranges."),
            ("selectionRangeProvider", "Provides smart selection expansion."),
            ("colorProvider", "Supplies document colors and color presentations."),
            ("documentLinkProvider", "Finds and resolves links in a document."),
            ("executeCommandProvider", "Accepts workspace/command execution requests."),
        ],
    ),
    (
        "Workspace & Files",
        [
            ("workspace", "Declares workspace-level features and dynamic registration support."),
            ("workspace.fileOperations", "Hooks file create/rename/delete operations."),
            ("workspace.workspaceFolders", "Supports multi-root workspace folders."),
            ("textDocumentSync", "Defines document open/change/save synchronization behavior."),
            ("notebookDocumentSync", "Supports notebook document synchronization."),
        ],
    ),
]


@dataclass
class ProbeResult:
    server_id: str
    transport: str
    launch_command: list[str]
    connect_target: str | None
    capabilities: dict[str, Any]
    server_info: dict[str, Any] | None
    raw_initialize: dict[str, Any]
    captured_at: str


class LspProtocolError(RuntimeError):
    """Raised on malformed LSP traffic."""


class UserFacingError(RuntimeError):
    """Raised for expected CLI failures that should be printed cleanly."""


class TimeoutError(RuntimeError):
    """Raised when an LSP operation exceeds the configured timeout."""


class StderrCollector:
    """Collect recent stderr output from a subprocess in the background."""

    def __init__(self, stream: BinaryIO | None, *, max_lines: int = 40) -> None:
        self._stream = stream
        self._lines: collections.deque[str] = collections.deque(maxlen=max_lines)
        self._thread: threading.Thread | None = None
        if stream is not None:
            self._thread = threading.Thread(target=self._drain, daemon=True)
            self._thread.start()

    def _drain(self) -> None:
        assert self._stream is not None
        while True:
            chunk = self._stream.readline()
            if not chunk:
                break
            self._lines.append(chunk.decode("utf-8", errors="replace").rstrip())

    def snapshot(self) -> str | None:
        lines = [line for line in self._lines if line]
        if not lines:
            return None
        return "\n".join(lines)


def supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM") not in {None, "dumb"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lsp-cap-inspect",
        description="Probe LSP server capabilities and render comparison reports.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Start or connect to an LSP server and save its capabilities.")
    probe.add_argument("--id", help="Stable key for storage. Defaults to the command name or connect target.")
    probe.add_argument("--transport", choices=["stdio", "tcp"], default="stdio")
    probe.add_argument("--connect", help="TCP target as host:port. Required for tcp transport.")
    probe.add_argument("--root-uri", default="file:///tmp/lsp-cap-inspect")
    probe.add_argument("--initialize-timeout", type=float, default=5.0)
    probe.add_argument(
        "server",
        nargs=argparse.REMAINDER,
        help="Server command after '--'. Example: probe -- pylsp --stdio",
    )

    report = subparsers.add_parser("report", help="Render a comparison report from saved probe data.")
    report.add_argument("--format", choices=["terminal", "html"], default="terminal")
    report.add_argument("--output", help="Write report to this file. Required for html, optional for terminal.")
    report.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY_PATH,
        help="Path to the saved JSON capability registry.",
    )

    return parser.parse_args(argv)


def split_host_port(target: str) -> tuple[str, int]:
    host, sep, port_text = target.rpartition(":")
    if not sep:
        raise SystemExit(f"Invalid --connect value '{target}'. Expected host:port.")
    try:
        return host, int(port_text)
    except ValueError as exc:
        raise SystemExit(f"Invalid TCP port in '{target}'.") from exc


def normalize_server_command(command: list[str]) -> list[str]:
    if command and command[0] == "--":
        command = command[1:]
    return command


def derive_server_id(explicit_id: str | None, command: list[str], connect_target: str | None) -> str:
    if explicit_id:
        return explicit_id
    if command:
        return Path(command[0]).name
    if connect_target:
        return connect_target.replace(":", "_")
    raise SystemExit("Unable to derive server id. Use --id.")


def jsonrpc_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise LspProtocolError("Unexpected end of stream while reading LSP payload.")
        chunks.extend(chunk)
    return bytes(chunks)


def read_lsp_message(stream: BinaryIO) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            raise LspProtocolError("Unexpected end of stream while reading LSP headers.")
        if line == b"\r\n":
            break
        name, sep, value = line.decode("ascii", errors="replace").partition(":")
        if not sep:
            raise LspProtocolError(f"Malformed LSP header line: {line!r}")
        headers[name.strip().lower()] = value.strip()

    try:
        content_length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise LspProtocolError("Missing or invalid Content-Length header.") from exc

    payload = read_exact(stream, content_length)
    return json.loads(payload.decode("utf-8"))


def read_lsp_message_with_timeout(stream: BinaryIO, timeout_s: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: BaseException | None = None

    def runner() -> None:
        nonlocal result, error
        try:
            result = read_lsp_message(stream)
        except BaseException as exc:  # pragma: no cover - passthrough from worker
            error = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError(f"Timed out waiting for LSP response after {timeout_s:.1f}s.")
    if error is not None:
        raise error
    return result


def send_request(
    writer: BinaryIO,
    reader: BinaryIO,
    method: str,
    params: dict[str, Any] | None,
    request_id: int,
    timeout_s: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    writer.write(jsonrpc_message(payload))
    writer.flush()

    while True:
        message = read_lsp_message_with_timeout(reader, timeout_s)
        if message.get("id") == request_id:
            if "error" in message:
                raise LspProtocolError(f"LSP error for {method}: {message['error']}")
            return message


def send_notification(writer: BinaryIO, method: str, params: dict[str, Any] | None) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    writer.write(jsonrpc_message(payload))
    writer.flush()


def wait_for_tcp(host: str, port: int, timeout_s: float) -> socket.socket:
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection((host, port), timeout=1.0)
            sock.settimeout(timeout_s)
            return sock
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise SystemExit(f"Timed out connecting to {host}:{port}: {last_error}")


def build_initialize_params(root_uri: str) -> dict[str, Any]:
    return {
        "processId": os.getpid(),
        "clientInfo": {"name": "lsp-cap-inspect", "version": "0.1.0"},
        "locale": "en-AU",
        "rootUri": root_uri,
        "capabilities": {
            "workspace": {
                "workspaceFolders": True,
                "configuration": True,
                "didChangeWatchedFiles": {"dynamicRegistration": True},
            },
            "textDocument": {
                "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                "completion": {
                    "dynamicRegistration": True,
                    "completionItem": {"snippetSupport": True, "documentationFormat": ["markdown", "plaintext"]},
                },
                "publishDiagnostics": {"relatedInformation": True},
                "semanticTokens": {
                    "dynamicRegistration": True,
                    "requests": {"range": True, "full": {"delta": True}},
                    "tokenTypes": ["namespace", "type", "class", "enum", "interface", "struct", "typeParameter"],
                    "tokenModifiers": ["declaration", "definition", "readonly", "static", "deprecated"],
                    "formats": ["relative"],
                },
                "inlayHint": {"dynamicRegistration": True},
            },
        },
        "workspaceFolders": [{"uri": root_uri, "name": "lsp-cap-inspect"}],
    }


def probe_server(args: argparse.Namespace) -> ProbeResult:
    command = normalize_server_command(args.server)
    connect_target = args.connect
    if args.transport == "tcp" and not connect_target:
        raise SystemExit("--connect is required when --transport tcp is used.")
    if args.transport == "stdio" and connect_target:
        raise SystemExit("--connect is only valid with --transport tcp.")
    if not command and args.transport == "stdio":
        raise SystemExit("A server command is required for stdio transport.")

    server_id = derive_server_id(args.id, command, connect_target)
    captured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    process: subprocess.Popen[bytes] | None = None
    socket_reader: BinaryIO | None = None
    socket_writer: BinaryIO | None = None
    sock: socket.socket | None = None
    process_reader: BinaryIO | None = None
    process_writer: BinaryIO | None = None
    process_stderr: BinaryIO | None = None
    stderr_collector: StderrCollector | None = None
    reader: BinaryIO
    writer: BinaryIO

    try:
        if args.transport == "stdio":
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                missing = command[0] if command else "<missing-command>"
                raise UserFacingError(
                    f"Unable to start language server '{missing}'. It was not found on PATH or at the given path."
                ) from exc
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None
            process_writer = process.stdin
            process_reader = process.stdout
            process_stderr = process.stderr
            stderr_collector = StderrCollector(process_stderr)
            writer = process_writer
            reader = process_reader
        else:
            if command:
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                except FileNotFoundError as exc:
                    missing = command[0]
                    raise UserFacingError(
                        f"Unable to start language server '{missing}'. It was not found on PATH or at the given path."
                    ) from exc
                assert process.stderr is not None
                process_stderr = process.stderr
                stderr_collector = StderrCollector(process_stderr)
            host, port = split_host_port(connect_target)
            try:
                sock = wait_for_tcp(host, port, args.initialize_timeout)
            except SystemExit as exc:
                raise UserFacingError(build_failure_message(str(exc), stderr_collector)) from exc
            socket_reader = sock.makefile("rb")
            socket_writer = sock.makefile("wb")
            reader = socket_reader
            writer = socket_writer

        try:
            initialize_response = send_request(
                writer=writer,
                reader=reader,
                method="initialize",
                params=build_initialize_params(args.root_uri),
                request_id=1,
                timeout_s=args.initialize_timeout,
            )
        except TimeoutError as exc:
            raise UserFacingError(
                build_failure_message(
                    f"The language server did not answer the initialize request within {args.initialize_timeout:.1f}s.",
                    stderr_collector,
                )
            ) from exc
        except LspProtocolError as exc:
            raise UserFacingError(build_failure_message(str(exc), stderr_collector)) from exc
        send_notification(writer, "initialized", {})
        try:
            send_request(
                writer=writer,
                reader=reader,
                method="shutdown",
                params=None,
                request_id=2,
                timeout_s=min(2.0, args.initialize_timeout),
            )
        except (LspProtocolError, TimeoutError):
            pass
        send_notification(writer, "exit", {})

        capabilities = initialize_response.get("result", {}).get("capabilities", {})
        server_info = initialize_response.get("result", {}).get("serverInfo")
        return ProbeResult(
            server_id=server_id,
            transport=args.transport,
            launch_command=command,
            connect_target=connect_target,
            capabilities=capabilities,
            server_info=server_info,
            raw_initialize=initialize_response,
            captured_at=captured_at,
        )
    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except Exception:
                process.kill()
        for handle in (socket_reader, socket_writer, process_reader, process_writer, process_stderr):
            if handle is not None:
                handle.close()
        if sock is not None:
            sock.close()


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_probe(path: Path, result: ProbeResult) -> None:
    registry = load_registry(path)
    registry[result.server_id] = {
        "server_id": result.server_id,
        "transport": result.transport,
        "launch_command": result.launch_command,
        "connect_target": result.connect_target,
        "server_info": result.server_info,
        "captured_at": result.captured_at,
        "capabilities": result.capabilities,
        "raw_initialize": result.raw_initialize,
    }
    path.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")


def provider_supported(value: Any) -> bool:
    if value in (None, False):
        return False
    return True


def nested_get(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def describe_text_document_sync(value: Any) -> str:
    if value is None:
        return "Not declared"
    if isinstance(value, int):
        kind = {0: "none", 1: "full", 2: "incremental"}.get(value, f"unknown({value})")
        return f"Sync kind: {kind}"
    if isinstance(value, dict):
        change = value.get("change")
        kind = {0: "none", 1: "full", 2: "incremental"}.get(change, f"unknown({change})")
        open_close = "yes" if value.get("openClose") else "no"
        save = value.get("save")
        save_text = "yes" if save else "no"
        return f"Sync kind: {kind}; openClose: {open_close}; save: {save_text}"
    return "Declared"


def summarize_provider(value: Any) -> str:
    if value is None:
        return "No"
    if value is True:
        return "Yes"
    if isinstance(value, dict):
        keys = ", ".join(sorted(value.keys())[:5])
        return f"Yes ({keys})" if keys else "Yes"
    if isinstance(value, list):
        return f"Yes ({len(value)} entries)"
    return f"Yes ({value})"


def build_probe_renderables(result: ProbeResult) -> list[Any]:
    info_lines = [
        f"[bold cyan]Server[/]: {result.server_id}",
        f"[bold cyan]Transport[/]: {result.transport}",
        f"[bold cyan]Captured[/]: {result.captured_at}",
    ]
    if result.server_info:
        info = result.server_info
        version = f" {info['version']}" if "version" in info else ""
        info_lines.append(f"[bold cyan]Server Info[/]: {info.get('name', 'unknown')}{version}")
    if result.launch_command:
        info_lines.append(f"[bold cyan]Launch[/]: {' '.join(result.launch_command)}")
    if result.connect_target:
        info_lines.append(f"[bold cyan]Connect[/]: {result.connect_target}")

    renderables: list[Any] = [Panel("\n".join(info_lines), title="LSP Capability Probe", border_style="bright_magenta")]
    for group_name, items in CAPABILITY_GROUPS:
        table = Table(title=group_name, expand=True, box=None, padding=(0, 1))
        table.add_column("Capability", style="bold cyan", ratio=24)
        table.add_column("Supported", style="bold", width=10)
        table.add_column("Details", ratio=26)
        table.add_column("Explanation", ratio=40)
        for capability_path, description in items:
            value = nested_get(result.capabilities, capability_path)
            supported = provider_supported(value)
            summary = describe_text_document_sync(value) if capability_path == "textDocumentSync" else summarize_provider(value)
            supported_text = Text("yes" if supported else "no", style="green" if supported else "red")
            table.add_row(capability_path, supported_text, summary, description)
        renderables.append(table)
    return renderables


def load_entries(path: Path) -> dict[str, dict[str, Any]]:
    data = load_registry(path)
    if not isinstance(data, dict):
        raise SystemExit(f"Registry at {path} must contain a JSON object keyed by server id.")
    return data


def build_terminal_report_renderables(entries: dict[str, dict[str, Any]]) -> list[Any]:
    server_names = sorted(entries)
    summary = Panel(
        f"[bold cyan]Servers[/]: {', '.join(server_names)}",
        title="LSP Capability Comparison",
        border_style="bright_magenta",
    )
    renderables: list[Any] = [summary]

    for group_name, items in CAPABILITY_GROUPS:
        table = Table(title=group_name, expand=True, box=None, padding=(0, 1))
        table.add_column("Capability", style="bold cyan", ratio=24)
        table.add_column("Explanation", ratio=40)
        table.add_column("Supported By", ratio=36)
        for capability_path, description in items:
            supporting = [
                server
                for server, payload in sorted(entries.items())
                if provider_supported(nested_get(payload.get("capabilities", {}), capability_path))
            ]
            support_text = Text(", ".join(supporting) if supporting else "none", style="green" if supporting else "red")
            table.add_row(capability_path, description, support_text)
        renderables.append(table)

    return renderables


def render_html_report(entries: dict[str, dict[str, Any]]) -> str:
    sections: list[str] = []
    for group_name, items in CAPABILITY_GROUPS:
        cards = []
        for capability_path, description in items:
            supporting = [
                server
                for server, payload in sorted(entries.items())
                if provider_supported(nested_get(payload.get("capabilities", {}), capability_path))
            ]
            chips = "".join(
                f'<span class="chip chip-yes">{html.escape(server)}</span>' for server in supporting
            ) or '<span class="chip chip-no">No saved server reports support this</span>'
            cards.append(
                f"""
                <article class="card">
                  <h3>{html.escape(capability_path)}</h3>
                  <p>{html.escape(description)}</p>
                  <div class="chips">{chips}</div>
                </article>
                """
            )
        sections.append(
            f"""
            <section>
              <h2>{html.escape(group_name)}</h2>
              <div class="grid">{''.join(cards)}</div>
            </section>
            """
        )

    updated = max((payload.get("captured_at", "") for payload in entries.values()), default="unknown")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LSP Capability Comparison</title>
  <style>
    :root {{
      --bg: #f7f1e8;
      --panel: rgba(255, 255, 255, 0.78);
      --panel-border: rgba(85, 61, 42, 0.16);
      --ink: #23180f;
      --muted: #6f5b4b;
      --accent: #c44d1a;
      --accent-soft: #f3c7af;
      --success: #2f7d4f;
      --danger: #8a3b29;
      --shadow: 0 24px 60px rgba(71, 45, 20, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(255, 212, 182, 0.7), transparent 34%),
        radial-gradient(circle at bottom right, rgba(206, 229, 212, 0.75), transparent 28%),
        linear-gradient(180deg, #fbf6ee 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .wrap {{
      width: min(1180px, calc(100% - 48px));
      margin: 0 auto;
      padding: 48px 0 72px;
    }}
    header {{
      padding: 28px 32px;
      border: 1px solid var(--panel-border);
      border-radius: 28px;
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    h1, h2, h3 {{ margin: 0; font-weight: 700; }}
    h1 {{ font-size: clamp(2.2rem, 5vw, 4rem); letter-spacing: -0.04em; }}
    h2 {{
      font-size: 1.8rem;
      margin: 40px 0 16px;
      letter-spacing: -0.03em;
    }}
    h3 {{ font-size: 1.1rem; margin-bottom: 12px; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .lede {{ margin-top: 10px; max-width: 70ch; }}
    .meta {{ margin-top: 18px; color: var(--accent); font-weight: 700; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
    }}
    .card {{
      padding: 20px;
      border-radius: 22px;
      border: 1px solid var(--panel-border);
      background: rgba(255,255,255,0.75);
      box-shadow: var(--shadow);
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    .chip {{
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 0.92rem;
      font-weight: 700;
    }}
    .chip-yes {{
      background: rgba(47, 125, 79, 0.14);
      color: var(--success);
    }}
    .chip-no {{
      background: rgba(138, 59, 41, 0.14);
      color: var(--danger);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>LSP Capability Comparison</h1>
      <p class="lede">This report groups common Language Server Protocol capabilities by purpose, explains what each feature means, and shows which saved server probes currently advertise support for it.</p>
      <div class="meta">Servers: {html.escape(', '.join(sorted(entries))) or 'none'} · Latest capture: {html.escape(updated)}</div>
    </header>
    {''.join(sections)}
  </div>
</body>
</html>
"""


def write_output(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text)
        print(f"Wrote report to {output}")
        return
    print(text, end="")


def print_rich(renderables: list[Any]) -> None:
    console = Console()
    for index, renderable in enumerate(renderables):
        if index:
            console.print()
        console.print(renderable)


def print_error(message: str) -> None:
    console = Console(stderr=True)
    console.print(Panel(message, title="Probe Failed", border_style="red"))


def build_failure_message(base_message: str, stderr_collector: StderrCollector | None) -> str:
    stderr_text = stderr_collector.snapshot() if stderr_collector is not None else None
    if not stderr_text:
        return base_message
    return f"{base_message}\n\nRecent stderr from the language server:\n{stderr_text}"


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        if args.command == "probe":
            result = probe_server(args)
            save_probe(REGISTRY_PATH, result)
            print_rich(build_probe_renderables(result))
            return 0

        entries = load_entries(args.registry)
        if not entries:
            raise UserFacingError(f"No saved probes found in {args.registry}. Run the 'probe' command first.")

        if args.format == "html":
            if not args.output:
                raise UserFacingError("--output is required when --format html is used.")
            write_output(render_html_report(entries), args.output)
            return 0

        if args.output:
            console = Console(record=True)
            for index, renderable in enumerate(build_terminal_report_renderables(entries)):
                if index:
                    console.print()
                console.print(renderable)
            write_output(console.export_text(), args.output)
            return 0

        print_rich(build_terminal_report_renderables(entries))
        return 0
    except UserFacingError as exc:
        print_error(str(exc))
        return 1
    except LspProtocolError as exc:
        print_error(f"The language server responded with invalid or unexpected LSP traffic.\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
