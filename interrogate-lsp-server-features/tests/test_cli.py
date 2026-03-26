from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from lsp_cap_inspect.cli import main


def find_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_probe_stdio_saves_registry(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    server = Path(__file__).with_name("mock_stdio_server.py")
    exit_code = main(["probe", "--", sys.executable, str(server)])
    assert exit_code == 0

    output = capsys.readouterr().out
    assert "Mock Stdio" in output
    registry = json.loads(Path("lsp-capabilities.json").read_text())
    key = Path(sys.executable).name
    assert registry[key]["capabilities"]["hoverProvider"] is True


def test_probe_tcp_and_html_report(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    server = Path(__file__).with_name("mock_tcp_server.py")
    port = find_free_port()

    probe_exit = main(
        [
            "probe",
            "--transport",
            "tcp",
            "--connect",
            f"127.0.0.1:{port}",
            "--",
            sys.executable,
            str(server),
            str(port),
        ]
    )
    assert probe_exit == 0

    report_path = tmp_path / "report.html"
    report_exit = main(["report", "--format", "html", "--output", str(report_path)])
    assert report_exit == 0
    report_html = report_path.read_text()
    assert "semanticTokensProvider" in report_html
    assert "python3" in report_html
    assert "Wrote report to" in capsys.readouterr().out


def test_probe_timeout_surfaces_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    server = Path(__file__).with_name("mock_hang_server.py")

    exit_code = main(
        [
            "probe",
            "--initialize-timeout",
            "0.1",
            "--",
            sys.executable,
            str(server),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "did not answer the initialize request within 0.1s" in captured.err
    assert "mock server started but will not answer initialize" in captured.err
