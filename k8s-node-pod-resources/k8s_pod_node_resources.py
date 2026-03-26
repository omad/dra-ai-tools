#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["rich>=14.0.0"]
# ///

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


console = Console()


@dataclass(frozen=True)
class ContainerRow:
    namespace: str
    pod: str
    container: str
    cpu: str
    memory: str
    request_cpu: str
    request_memory: str
    limit_cpu: str
    limit_memory: str


def run_kubectl(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_cpu(value: str) -> int:
    if not value:
        return 0
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def format_cpu(millicores: int) -> str:
    if millicores == 0:
        return ""
    if millicores % 1000 == 0:
        return str(millicores // 1000)
    return f"{millicores}m"


MEMORY_SUFFIXES = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "Ei": 1024**6,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
    "P": 1000**5,
    "E": 1000**6,
}


def parse_memory(value: str) -> int:
    if not value:
        return 0
    for suffix, multiplier in MEMORY_SUFFIXES.items():
        if value.endswith(suffix):
            number = float(value[: -len(suffix)])
            return int(number * multiplier)
    return int(value)


def format_memory(bytes_value: int) -> str:
    if bytes_value == 0:
        return ""
    units = ["Ki", "Mi", "Gi", "Ti", "Pi", "Ei"]
    value = float(bytes_value)
    for unit in units:
        value /= 1024
        if value < 1024 or unit == units[-1]:
            if value.is_integer():
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
    return f"{bytes_value}B"


def style_percent(part: int, whole: int) -> Text:
    if whole <= 0:
        return Text("-")
    pct = (part / whole) * 100
    if pct >= 90:
        style = "bold red"
    elif pct >= 75:
        style = "bold yellow"
    else:
        style = "bold green"
    return Text(f"{pct:.1f}%", style=style)


def style_usage(actual: str, requested: str, parser) -> Text:
    text = Text(actual or "-")
    actual_value = parser(actual)
    requested_value = parser(requested)
    if requested_value > 0 and actual_value < (requested_value * 0.5):
        text.stylize("bold black on yellow")
    return text


def load_metrics() -> dict[tuple[str, str, str], tuple[str, str]]:
    metrics_by_key: dict[tuple[str, str, str], tuple[str, str]] = {}
    top_output = run_kubectl("top", "pod", "-A", "--containers", "--no-headers")
    for line in top_output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        namespace, pod, container, cpu, memory = fields[:5]
        metrics_by_key[(namespace, pod, container)] = (cpu, memory)
    return metrics_by_key


def build_container_rows(
    pod_items: Iterable[dict], metrics_by_key: dict[tuple[str, str, str], tuple[str, str]]
) -> list[ContainerRow]:
    rows: list[ContainerRow] = []
    for item in pod_items:
        namespace = item["metadata"]["namespace"]
        pod = item["metadata"]["name"]

        for container in item["spec"].get("containers", []):
            name = container["name"]
            resources = container.get("resources", {})
            requests = resources.get("requests", {})
            limits = resources.get("limits", {})
            cpu, memory = metrics_by_key.get((namespace, pod, name), ("", ""))

            rows.append(
                ContainerRow(
                    namespace=namespace,
                    pod=pod,
                    container=name,
                    cpu=cpu,
                    memory=memory,
                    request_cpu=requests.get("cpu", ""),
                    request_memory=requests.get("memory", ""),
                    limit_cpu=limits.get("cpu", ""),
                    limit_memory=limits.get("memory", ""),
                )
            )
    return rows


def summarize_rows(rows: Iterable[ContainerRow]) -> ContainerRow:
    total_cpu = 0
    total_memory = 0
    total_request_cpu = 0
    total_request_memory = 0
    total_limit_cpu = 0
    total_limit_memory = 0

    for row in rows:
        total_cpu += parse_cpu(row.cpu)
        total_memory += parse_memory(row.memory)
        total_request_cpu += parse_cpu(row.request_cpu)
        total_request_memory += parse_memory(row.request_memory)
        total_limit_cpu += parse_cpu(row.limit_cpu)
        total_limit_memory += parse_memory(row.limit_memory)

    return ContainerRow(
        namespace="TOTAL",
        pod="",
        container="",
        cpu=format_cpu(total_cpu),
        memory=format_memory(total_memory),
        request_cpu=format_cpu(total_request_cpu),
        request_memory=format_memory(total_request_memory),
        limit_cpu=format_cpu(total_limit_cpu),
        limit_memory=format_memory(total_limit_memory),
    )


def render_container_table(rows: list[ContainerRow], totals: ContainerRow, node_name: str) -> Table:
    table = Table(
        title=f"Pod Resources on {node_name}",
        box=box.SIMPLE_HEAVY,
        header_style="bold cyan",
        title_style="bold white",
        pad_edge=False,
    )
    table.add_column("Namespace", style="bold", no_wrap=True)
    table.add_column("Pod", no_wrap=True)
    table.add_column("Container", no_wrap=True)
    table.add_column("CPU", justify="right", no_wrap=True)
    table.add_column("Req CPU", justify="right", no_wrap=True)
    table.add_column("Lim CPU", justify="right", no_wrap=True)
    table.add_column("Memory", justify="right", no_wrap=True)
    table.add_column("Req Mem", justify="right", no_wrap=True)
    table.add_column("Lim Mem", justify="right", no_wrap=True)

    for row in rows:
        table.add_row(
            row.namespace,
            row.pod,
            row.container,
            style_usage(row.cpu, row.request_cpu, parse_cpu),
            row.request_cpu,
            row.limit_cpu,
            style_usage(row.memory, row.request_memory, parse_memory),
            row.request_memory,
            row.limit_memory,
        )

    if rows:
        table.add_section()
    table.add_row(
        f"[bold]TOTAL[/bold]",
        "",
        "",
        f"[bold]{totals.cpu}[/bold]",
        f"[bold]{totals.request_cpu}[/bold]",
        f"[bold]{totals.limit_cpu}[/bold]",
        f"[bold]{totals.memory}[/bold]",
        f"[bold]{totals.request_memory}[/bold]",
        f"[bold]{totals.limit_memory}[/bold]",
        style="bold white",
    )
    return table


def build_node_summary(node: dict, totals: ContainerRow) -> Panel:
    status = node.get("status", {})
    allocatable = status.get("allocatable", {})
    capacity = status.get("capacity", {})

    used_cpu = parse_cpu(totals.cpu)
    used_memory = parse_memory(totals.memory)
    alloc_cpu = parse_cpu(allocatable.get("cpu", ""))
    alloc_memory = parse_memory(allocatable.get("memory", ""))
    cap_cpu = parse_cpu(capacity.get("cpu", ""))
    cap_memory = parse_memory(capacity.get("memory", ""))

    summary = Table(box=box.MINIMAL_DOUBLE_HEAD, header_style="bold magenta")
    summary.add_column("Metric", style="bold", no_wrap=True)
    summary.add_column("CPU", justify="right", no_wrap=True)
    summary.add_column("Memory", justify="right", no_wrap=True)

    summary.add_row("Pod usage", format_cpu(used_cpu), format_memory(used_memory))
    summary.add_row(
        "Available alloc",
        format_cpu(max(alloc_cpu - used_cpu, 0)),
        format_memory(max(alloc_memory - used_memory, 0)),
    )
    summary.add_row("Allocatable", format_cpu(alloc_cpu), format_memory(alloc_memory))
    summary.add_row("Utilized alloc", style_percent(used_cpu, alloc_cpu), style_percent(used_memory, alloc_memory))
    summary.add_section()
    summary.add_row(
        "Available cap",
        format_cpu(max(cap_cpu - used_cpu, 0)),
        format_memory(max(cap_memory - used_memory, 0)),
    )
    summary.add_row("Capacity", format_cpu(cap_cpu), format_memory(cap_memory))
    summary.add_row("Utilized cap", style_percent(used_cpu, cap_cpu), style_percent(used_memory, cap_memory))

    subtitle = Text()
    subtitle.append("Pod usage matches the TOTAL row above. ", style="dim")
    subtitle.append("Highlighted usage cells are below 50% of requested resources.", style="dim")

    return Panel(
        Group(summary, subtitle),
        title="Node Summary",
        title_align="left",
        border_style="bright_blue",
        padding=(0, 1),
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: uv run k8s_pod_node_resources.py NODE", file=sys.stderr)
        return 1

    node_name = sys.argv[1]

    with ThreadPoolExecutor(max_workers=3) as executor:
        pods_future = executor.submit(
            run_kubectl,
            "get",
            "pods",
            "-A",
            f"--field-selector=spec.nodeName={node_name}",
            "-o",
            "json",
        )
        metrics_future = executor.submit(load_metrics)
        node_future = executor.submit(run_kubectl, "get", "node", node_name, "-o", "json")

        pod_items = json.loads(pods_future.result())["items"]
        metrics_by_key = metrics_future.result()
        node_json = json.loads(node_future.result())

    rows = sorted(build_container_rows(pod_items, metrics_by_key), key=lambda row: (row.namespace, row.pod, row.container))
    totals = summarize_rows(rows)

    console.print(render_container_table(rows, totals, node_name))
    console.print()
    console.print(build_node_summary(node_json, totals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
