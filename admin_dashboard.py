#!/usr/bin/env python3
"""
GLOBE-CLI — Real-Time Admin Dashboard (Terminal)
Uses Rich.Live to display live server metrics in a cyber-blue themed terminal UI.
"""

import os
import sys
import time

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from dotenv import load_dotenv
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv()

API_KEY = os.getenv("GLOBE_API_KEY", "")
BASE_URL = os.getenv("GLOBE_TUNNEL_URL") or f"http://localhost:{os.getenv('GLOBE_PORT', '8787')}"

console = Console(force_terminal=True)

BANNER = r"""[bold cyan]
   ██████╗ ██╗      ██████╗ ██████╗ ███████╗       ██████╗██╗     ██╗
  ██╔════╝ ██║     ██╔═══██╗██╔══██╗██╔════╝      ██╔════╝██║     ██║
  ██║  ███╗██║     ██║   ██║██████╔╝█████╗  █████╗██║     ██║     ██║
  ██║   ██║██║     ██║   ██║██╔══██╗██╔══╝  ╚════╝██║     ██║     ██║
  ╚██████╔╝███████╗╚██████╔╝██████╔╝███████╗      ╚██████╗███████╗██║
   ╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝       ╚═════╝╚══════╝╚═╝
[/bold cyan]
[dim]  Admin Dashboard  •  Press Ctrl+C to exit[/dim]
"""


def fetch_metrics() -> dict | None:
    """Fetch metrics from the GLOBE-CLI server."""
    try:
        resp = httpx.get(
            f"{BASE_URL.rstrip('/')}/metrics",
            headers={"X-API-KEY": API_KEY},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def build_dashboard(data: dict | None, tick: int) -> Layout:
    """Build the full Rich layout for the dashboard."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=1),
        Layout(name="right", ratio=2),
    )

    # Header
    frames = ["🌍", "🌎", "🌏"]
    globe = frames[tick % len(frames)]
    layout["header"].update(
        Panel(
            Text(f"{globe}  GLOBE-CLI Admin Dashboard  {globe}", justify="center", style="bold cyan"),
            border_style="cyan",
        )
    )

    if data is None:
        layout["left"].update(Panel("[bold red]⚠ Server Unreachable[/bold red]", border_style="red"))
        layout["right"].update(Panel("[dim]Waiting for connection...[/dim]", border_style="dim"))
        layout["footer"].update(
            Panel(Text("Retrying...", justify="center", style="yellow"), border_style="yellow")
        )
        return layout

    # ── Left column: Stats cards ──
    uptime_s = data.get("uptime_seconds", 0)
    uptime_str = f"{int(uptime_s // 3600)}h {int((uptime_s % 3600) // 60)}m {int(uptime_s % 60)}s"

    cpu = data.get("cpu_percent", 0)
    mem = data.get("memory_percent", 0)
    cpu_bar = _progress_bar(cpu, color="cyan" if cpu < 80 else "red")
    mem_bar = _progress_bar(mem, color="cyan" if mem < 85 else "red")

    stats_text = (
        f"[bold cyan]📊 Metrics[/bold cyan]\n\n"
        f"  [white]Requests:[/white]     [bold green]{data.get('total_requests', 0)}[/bold green]\n"
        f"  [white]Tokens:[/white]       [bold green]{data.get('total_tokens', 0):,}[/bold green]\n"
        f"  [white]Active:[/white]       [bold yellow]{data.get('active_connections', 0)}[/bold yellow]\n"
        f"  [white]Cost Saved:[/white]   [bold green]${data.get('cost_saved_usd', 0):.4f}[/bold green]\n\n"
        f"[bold cyan]🖥  System[/bold cyan]\n\n"
        f"  [white]CPU:[/white]  {cpu_bar}  {cpu:.0f}%\n"
        f"  [white]MEM:[/white]  {mem_bar}  {mem:.0f}%\n"
        f"  [white]GPU:[/white]  [dim]{data.get('gpu_info', 'N/A')}[/dim]\n\n"
        f"  [white]Uptime:[/white] {uptime_str}"
    )
    layout["left"].update(Panel(stats_text, border_style="cyan", title="System"))

    # ── Right column: Recent requests table ──
    table = Table(
        title="🌐 Recent Global Requests",
        border_style="cyan",
        header_style="bold cyan",
        expand=True,
        show_lines=False,
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("IP Address", style="white", min_width=16)
    table.add_column("Timestamp (UTC)", style="dim")

    recent = data.get("recent_requests", [])
    if recent:
        for i, entry in enumerate(recent[:15], 1):
            table.add_row(str(i), entry.get("ip", "?"), entry.get("timestamp", "?"))
    else:
        table.add_row("—", "No requests yet", "—")

    layout["right"].update(Panel(table, border_style="cyan", title="Activity"))

    # Footer
    layout["footer"].update(
        Panel(
            Text(f"Server: {BASE_URL}  •  Refresh: 2s  •  Ctrl+C to exit", justify="center", style="dim"),
            border_style="dim",
        )
    )

    return layout


def _progress_bar(pct: float, width: int = 20, color: str = "cyan") -> str:
    """Generate a text-based progress bar."""
    filled = int(width * pct / 100)
    empty = width - filled
    return f"[{color}]{'█' * filled}{'░' * empty}[/{color}]"


def main():
    if not API_KEY:
        console.print("[bold red]No API key.[/bold red] Set GLOBE_API_KEY in .env")
        sys.exit(1)

    console.print(BANNER)
    console.print(f"  [dim]Server: {BASE_URL}[/dim]\n")

    tick = 0
    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                data = fetch_metrics()
                layout = build_dashboard(data, tick)
                live.update(layout)
                tick += 1
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[cyan]Dashboard closed.[/cyan]")


if __name__ == "__main__":
    main()
