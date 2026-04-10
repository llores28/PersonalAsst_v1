"""
Shared utilities for the Bootstrap CLI Toolkit.
Structured output, logging, error handling.
"""

import json
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import click
import yaml


class OutputFormat(str, Enum):
    JSON = "json"
    HUMAN = "human"
    YAML = "yaml"


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    WARN = "warn"
    PARTIAL = "partial"
    INFO = "info"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


def emit(data: dict[str, Any], fmt: OutputFormat = OutputFormat.JSON) -> None:
    """Emit structured output in the requested format."""
    if fmt == OutputFormat.JSON:
        click.echo(json.dumps(data, indent=2, default=str))
    elif fmt == OutputFormat.YAML:
        click.echo(yaml.dump(data, default_flow_style=False, sort_keys=False))
    else:
        _emit_human(data)


def _emit_human(data: dict[str, Any], indent: int = 0) -> None:
    """Pretty-print structured data for human consumption."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import print as rprint

        console = Console()

        if "title" in data:
            console.print(f"\n[bold]{data['title']}[/bold]")

        if "status" in data:
            status = data["status"]
            color = {
                "pass": "green", "fail": "red", "skip": "yellow",
                "warn": "yellow", "partial": "cyan", "info": "blue",
            }.get(status, "white")
            console.print(f"  Status: [{color}]{status}[/{color}]")

        if "items" in data and isinstance(data["items"], list):
            table = Table(show_header=True)
            if data["items"]:
                for key in data["items"][0].keys():
                    table.add_column(str(key))
                for item in data["items"]:
                    table.add_row(*[str(v) for v in item.values()])
                console.print(table)

        if "message" in data:
            console.print(f"  {data['message']}")

        if "details" in data and isinstance(data["details"], dict):
            for k, v in data["details"].items():
                console.print(f"  [dim]{k}:[/dim] {v}")

    except ImportError:
        click.echo(json.dumps(data, indent=2, default=str))


def make_result(
    tool: str,
    status: Status,
    message: str = "",
    items: Optional[list[dict]] = None,
    details: Optional[dict] = None,
    duration_ms: Optional[int] = None,
) -> dict[str, Any]:
    """Build a standardized result dict."""
    result: dict[str, Any] = {
        "tool": tool,
        "status": status.value,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if message:
        result["message"] = message
    if items is not None:
        result["items"] = items
    if details is not None:
        result["details"] = details
    if duration_ms is not None:
        result["duration_ms"] = duration_ms
    return result


def truncate_output(text: str, max_chars: int = 500) -> str:
    """Truncate long output to prevent context bloat."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n... [truncated {len(text) - max_chars} chars] ...\n" + text[-half:]


def find_project_root(start: Optional[Path] = None) -> Path:
    """Walk up from start to find the project root (contains .windsurf/ or .git/)."""
    current = start or Path.cwd()
    for parent in [current] + list(current.parents):
        if (parent / ".windsurf").is_dir() or (parent / ".git").is_dir():
            return parent
    return current


def format_option(func):
    """Decorator to add --format option to a click command."""
    return click.option(
        "--format", "output_format",
        type=click.Choice(["json", "human", "yaml"], case_sensitive=False),
        default="json",
        help="Output format (default: json for model consumption).",
    )(func)
