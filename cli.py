#!/usr/bin/env python3
"""
GLOBE-CLI — Premium CLI Client
Streams AI responses with syntax-highlighted code blocks and Rich UI.
"""

import json
import os
import re
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
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

load_dotenv()

app = typer.Typer(
    name="globe-cli",
    help="🌐 GLOBE-CLI — Global AI Coding Assistant",
    add_completion=False,
)
console = Console(force_terminal=True)

API_KEY = os.getenv("GLOBE_API_KEY", "")
BASE_URL = os.getenv("GLOBE_TUNNEL_URL") or f"http://localhost:{os.getenv('GLOBE_PORT', '8787')}"

BANNER = r"""[bold cyan]
   ██████╗ ██╗      ██████╗ ██████╗ ███████╗       ██████╗██╗     ██╗
  ██╔════╝ ██║     ██╔═══██╗██╔══██╗██╔════╝      ██╔════╝██║     ██║
  ██║  ███╗██║     ██║   ██║██████╔╝█████╗  █████╗██║     ██║     ██║
  ██║   ██║██║     ██║   ██║██╔══██╗██╔══╝  ╚════╝██║     ██║     ██║
  ╚██████╔╝███████╗╚██████╔╝██████╔╝███████╗      ╚██████╗███████╗██║
   ╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝       ╚═════╝╚══════╝╚═╝
[/bold cyan]"""

AGENT_STYLES = {
    "architect": {"icon": "📐", "color": "bright_cyan", "label": "Architect"},
    "coder": {"icon": "💻", "color": "bright_green", "label": "Coder"},
    "reviewer": {"icon": "🔍", "color": "bright_yellow", "label": "Reviewer"},
}


def _request_with_retry(method: str, url: str, max_retries: int = 5, **kwargs) -> httpx.Response:
    """HTTP request with exponential backoff."""
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
                resp = getattr(client, method)(url, **kwargs)
                resp.raise_for_status()
                return resp
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
            if attempt == max_retries - 1:
                raise
            wait = min(2 ** attempt, 30)
            console.print(f"  [yellow]Connection failed, retrying in {wait}s... ({e})[/yellow]")
            time.sleep(wait)


def _stream_with_retry(url: str, payload: dict, headers: dict, max_retries: int = 5):
    """SSE streaming request with exponential backoff."""
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    yield from resp.iter_lines()
                return
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt == max_retries - 1:
                raise
            wait = min(2 ** attempt, 30)
            console.print(f"  [yellow]Stream interrupted, retrying in {wait}s... ({e})[/yellow]")
            time.sleep(wait)


def _render_code_blocks(text: str):
    """Print text with syntax-highlighted code fences."""
    pattern = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
    last_end = 0
    for match in pattern.finditer(text):
        pre = text[last_end:match.start()]
        if pre.strip():
            console.print(Markdown(pre))
        lang = match.group(1) or "python"
        code = match.group(2).rstrip()
        console.print(Syntax(code, lang, theme="monokai", line_numbers=True, padding=1))
        last_end = match.end()
    remaining = text[last_end:]
    if remaining.strip():
        console.print(Markdown(remaining))


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Your coding request"),
    context: str = typer.Option("", "--context", "-c", help="Additional context"),
    server: str = typer.Option("", "--server", "-s", help="Override server URL"),
    key: str = typer.Option("", "--key", "-k", help="Override API key"),
):
    """Send a coding request to the GLOBE-CLI 3-agent pipeline."""
    base = server or BASE_URL
    api_key = key or API_KEY

    if not api_key:
        console.print("[bold red]No API key configured.[/bold red] Set GLOBE_API_KEY in .env or use --key")
        raise typer.Exit(1)

    console.print(BANNER)
    console.print(Panel(f"[white]{prompt}[/white]", title="📨 Your Request", border_style="cyan"))
    console.print()

    headers = {"X-API-KEY": api_key, "Accept": "text/event-stream"}
    payload = {"prompt": prompt, "context": context}
    url = f"{base.rstrip('/')}/generate"

    current_agent = ""
    agent_buffer = ""
    token_count = 0
    start_time = time.time()

    try:
        for line in _stream_with_retry(url, payload, headers):
            line = line.strip()
            if not line:
                continue

            field, _, value = line.partition(":")
            if field == "event":
                event_type = value.strip()
            elif field == "data":
                data = json.loads(value.strip())

                if event_type == "agent":
                    agent_name = data.get("agent", "")
                    status = data.get("status", "")

                    if status == "start":
                        if agent_buffer and current_agent:
                            _render_agent_output(current_agent, agent_buffer)
                        current_agent = agent_name
                        agent_buffer = ""
                        style = AGENT_STYLES.get(agent_name, {})
                        icon = style.get("icon", "🤖")
                        color = style.get("color", "white")
                        label = style.get("label", agent_name)
                        console.print(
                            f"\n  [{color}]{icon} {label} Agent — Processing...[/{color}]"
                        )
                        console.print(f"  [dim]{'─' * 50}[/dim]")

                    elif status == "done":
                        if agent_buffer and current_agent:
                            _render_agent_output(current_agent, agent_buffer)
                            agent_buffer = ""
                        style = AGENT_STYLES.get(agent_name, {})
                        color = style.get("color", "white")
                        label = style.get("label", agent_name)
                        console.print(f"  [{color}]✅ {label} Agent — Complete[/{color}]")

                elif event_type == "token":
                    token = data.get("token", "")
                    agent_buffer += token
                    token_count += 1

                elif event_type == "done":
                    elapsed = time.time() - start_time
                    console.print()
                    console.print(Panel(
                        f"[bold green]Pipeline Complete[/bold green]\n"
                        f"  Tokens: {token_count}  •  Time: {elapsed:.1f}s  •  "
                        f"Tokens/s: {token_count / elapsed:.1f}",
                        title="🏁 Summary",
                        border_style="green",
                    ))

                elif event_type == "error":
                    console.print(f"\n[bold red]Error: {data.get('error', 'Unknown')}[/bold red]")

    except httpx.ConnectError:
        console.print("[bold red]Cannot connect to GLOBE-CLI server.[/bold red]")
        console.print(f"[dim]Tried: {url}[/dim]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        raise typer.Exit(0)


def _render_agent_output(agent: str, text: str):
    """Render an agent's accumulated output with code highlighting."""
    style = AGENT_STYLES.get(agent, {})
    color = style.get("color", "white")
    _render_code_blocks(text)


@app.command()
def health(
    server: str = typer.Option("", "--server", "-s", help="Override server URL"),
    key: str = typer.Option("", "--key", "-k", help="Override API key"),
):
    """Check if the GLOBE-CLI server is reachable."""
    base = server or BASE_URL
    try:
        resp = _request_with_retry("get", f"{base.rstrip('/')}/health")
        data = resp.json()
        console.print(Panel(
            f"[bold green]Server Online[/bold green]\n"
            f"  Service: {data.get('service')}\n"
            f"  Tunnel:  {data.get('tunnel') or 'N/A'}",
            title="🏥 Health Check",
            border_style="green",
        ))
    except Exception as e:
        console.print(f"[bold red]Server unreachable:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def stats(
    server: str = typer.Option("", "--server", "-s", help="Override server URL"),
    key: str = typer.Option("", "--key", "-k", help="Override API key"),
):
    """Show server metrics."""
    base = server or BASE_URL
    api_key = key or API_KEY
    try:
        resp = _request_with_retry(
            "get", f"{base.rstrip('/')}/metrics",
            headers={"X-API-KEY": api_key},
        )
        data = resp.json()
        uptime_h = int(data["uptime_seconds"] // 3600)
        uptime_m = int((data["uptime_seconds"] % 3600) // 60)
        console.print(Panel(
            f"[cyan]Requests:[/cyan]    {data['total_requests']}\n"
            f"[cyan]Tokens:[/cyan]      {data['total_tokens']:,}\n"
            f"[cyan]Active:[/cyan]      {data['active_connections']}\n"
            f"[cyan]Cost Saved:[/cyan]  ${data['cost_saved_usd']:.4f}\n"
            f"[cyan]CPU:[/cyan]         {data['cpu_percent']}%\n"
            f"[cyan]Memory:[/cyan]      {data['memory_percent']}%\n"
            f"[cyan]GPU:[/cyan]         {data['gpu_info']}\n"
            f"[cyan]Uptime:[/cyan]      {uptime_h}h {uptime_m}m",
            title="📊 Server Metrics",
            border_style="cyan",
        ))
    except Exception as e:
        console.print(f"[bold red]Failed to fetch metrics:[/bold red] {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
