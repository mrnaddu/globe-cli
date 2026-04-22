#!/usr/bin/env python3
"""
GLOBE-CLI — No-Touch Automated Installer
Handles venv creation, dependency installation, Ollama model pulls,
Cloudflare tunnel setup, and .env generation.
"""

import subprocess
import sys
import os
import shutil
import time
import re
import secrets
import threading

# Force UTF-8 on Windows to support Unicode box-drawing and emoji
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BANNER = r"""
[bold cyan]
   ██████╗ ██╗      ██████╗ ██████╗ ███████╗       ██████╗██╗     ██╗
  ██╔════╝ ██║     ██╔═══██╗██╔══██╗██╔════╝      ██╔════╝██║     ██║
  ██║  ███╗██║     ██║   ██║██████╔╝█████╗  █████╗██║     ██║     ██║
  ██║   ██║██║     ██║   ██║██╔══██╗██╔══╝  ╚════╝██║     ██║     ██║
  ╚██████╔╝███████╗╚██████╔╝██████╔╝███████╗      ╚██████╗███████╗██║
   ╚═════╝ ╚══════╝ ╚═════╝ ╚═════╝ ╚══════╝       ╚═════╝╚══════╝╚═╝
[/bold cyan]
[dim white]  Self-Deploying Global AI Ecosystem  •  Automated Installer[/dim white]
"""

WORLD_MAP = r"""
[bold cyan]
          . _  .  _  .  _ .  _  .  _  .  _  .  _  .  _  .
       .::'  `.::'  `.::'  `.::'  `.::'  `.::'  `.::'  `.::
      ::  ╔══════════════════════════════════════════╗   ::
      ::  ║     ·  .  ·  🌐  GLOBE-CLI  🌐  ·  .  · ║   ::
      ::  ║  ▄▄▄▄      ▄   ▄▄▄▄▄      ▄▄             ║   ::
      ::  ║ █    █▄ ▄▄█ █▄█     █▄▄▄██  █▄▄           ║   ::
      ::  ║ █▄    ██  ▄  ██  ▄▄  █   █▄  ▄██▄         ║   ::
      ::  ║  █▄▄  ▀█▄▄█  █  ██  █    ▀██▀  █▄▄▄       ║   ::
      ::  ║   ▀▀█   ▀▀    ▀▀  ▀▀      ▀     ▀▀▀       ║   ::
      ::  ║    ▀▀▀▀                                    ║   ::
      ::  ║  Serving AI Globally • Zero Cloud Cost     ║   ::
      ::  ╚══════════════════════════════════════════╝   ::
       '::.  .::.  .::.  .::.  .::.  .::.  .::.  .::.  .::'
          `'  `'  `'  `'  `'  `'  `'  `'  `'  `'  `'  `'
[/bold cyan]"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, ".venv")
ENV_FILE = os.path.join(BASE_DIR, ".env")
REQ_FILE = os.path.join(BASE_DIR, "requirements.txt")

IS_WINDOWS = sys.platform == "win32"
PYTHON_BIN = os.path.join(VENV_DIR, "Scripts" if IS_WINDOWS else "bin", "python")
PIP_BIN = os.path.join(VENV_DIR, "Scripts" if IS_WINDOWS else "bin", "pip")


def run(cmd: list[str], capture: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        cwd=BASE_DIR,
    )


def main():
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "rich"], check=True)
        from rich.console import Console
        from rich.panel import Panel
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    console = Console(force_terminal=True)
    console.print(BANNER)
    console.print(WORLD_MAP)

    steps = [
        "Creating virtual environment",
        "Installing Python dependencies",
        "Verifying Ollama installation",
        "Pulling llama3.2:3b model",
        "Pulling qwen2.5-coder:7b model",
        "Generating .env configuration",
        "Starting Cloudflare tunnel",
    ]

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="bold green"),
        console=console,
        transient=False,
    ) as progress:
        master = progress.add_task("GLOBE-CLI Setup", total=len(steps))

        # ── Step 1: Venv ──
        progress.update(master, description=steps[0])
        if not os.path.isdir(VENV_DIR):
            run([sys.executable, "-m", "venv", VENV_DIR])
        progress.advance(master)

        # ── Step 2: Dependencies ──
        progress.update(master, description=steps[1])
        run([PIP_BIN, "install", "--upgrade", "pip"], capture=True)
        run([PIP_BIN, "install", "-r", REQ_FILE], capture=True)
        progress.advance(master)

        # ── Step 3: Ollama ──
        progress.update(master, description=steps[2])
        ollama_path = shutil.which("ollama")
        if not ollama_path:
            console.print(
                Panel(
                    "[bold red]Ollama not found![/bold red]\n"
                    "Install from [link=https://ollama.com]https://ollama.com[/link] and re-run setup.",
                    title="⚠ Missing Dependency",
                    border_style="red",
                )
            )
            sys.exit(1)
        result = run(["ollama", "list"], capture=True)
        console.print(f"  [dim]Ollama found at: {ollama_path}[/dim]")
        progress.advance(master)

        # ── Step 4 & 5: Pull models ──
        models = ["llama3.2:3b", "qwen2.5-coder:7b"]
        for i, model in enumerate(models):
            progress.update(master, description=steps[3 + i])
            installed = result.stdout if result.returncode == 0 else ""
            if model.split(":")[0] in installed:
                console.print(f"  [dim]{model} already available[/dim]")
            else:
                console.print(f"  [yellow]Pulling {model} (this may take a while)...[/yellow]")
                run(["ollama", "pull", model], timeout=1800)
            progress.advance(master)

        # ── Step 6: .env ──
        progress.update(master, description=steps[5])
        api_key = secrets.token_urlsafe(32)
        env_vars = {
            "GLOBE_API_KEY": api_key,
            "GLOBE_HOST": "0.0.0.0",
            "GLOBE_PORT": "8787",
            "GLOBE_TUNNEL_URL": "",
            "OLLAMA_HOST": "http://localhost:11434",
            "MODEL_ARCHITECT": "llama3.2:3b",
            "MODEL_CODER": "qwen2.5-coder:7b",
            "MODEL_REVIEWER": "llama3.2:3b",
        }
        if os.path.isfile(ENV_FILE):
            from dotenv import dotenv_values
            existing = dotenv_values(ENV_FILE)
            for k, v in existing.items():
                if v:
                    env_vars[k] = v

        with open(ENV_FILE, "w") as f:
            for k, v in env_vars.items():
                f.write(f"{k}={v}\n")
        console.print(f"  [dim]API Key: {env_vars['GLOBE_API_KEY'][:12]}...[/dim]")
        progress.advance(master)

        # ── Step 7: Cloudflare Tunnel ──
        progress.update(master, description=steps[6])
        cf_path = shutil.which("cloudflared")
        tunnel_url = ""
        if cf_path:
            console.print("  [dim]Launching cloudflared tunnel...[/dim]")
            tunnel_url = _start_tunnel(int(env_vars["GLOBE_PORT"]), console)
            if tunnel_url:
                env_vars["GLOBE_TUNNEL_URL"] = tunnel_url
                with open(ENV_FILE, "w") as f:
                    for k, v in env_vars.items():
                        f.write(f"{k}={v}\n")
        else:
            console.print(
                "  [yellow]cloudflared not found — tunnel skipped.[/yellow]\n"
                "  [dim]Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/[/dim]"
            )
        progress.advance(master)

    # ── Summary ──
    console.print()
    console.print(
        Panel(
            f"[bold green]✅ GLOBE-CLI Setup Complete![/bold green]\n\n"
            f"  [cyan]API Key:[/cyan]     {env_vars['GLOBE_API_KEY'][:16]}...\n"
            f"  [cyan]Local URL:[/cyan]   http://localhost:{env_vars['GLOBE_PORT']}\n"
            f"  [cyan]Tunnel URL:[/cyan]  {tunnel_url or 'N/A (install cloudflared)'}\n"
            f"  [cyan]Models:[/cyan]      llama3.2:3b, qwen2.5-coder:7b\n\n"
            f"  [bold white]Launch server:[/bold white]  .venv\\Scripts\\python server.py\n"
            f"  [bold white]Launch CLI:[/bold white]     .venv\\Scripts\\python cli.py\n"
            f"  [bold white]Dashboard:[/bold white]      .venv\\Scripts\\python admin_dashboard.py",
            title="🌐 GLOBE-CLI",
            border_style="bold cyan",
        )
    )


def _start_tunnel(port: int, console) -> str:
    """Launch cloudflared quick tunnel and capture the public URL."""
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        match = re.search(r"(https://[a-z0-9\-]+\.trycloudflare\.com)", line)
        if match:
            url = match.group(1)
            console.print(f"  [bold green]Tunnel: {url}[/bold green]")
            break
    # Keep the tunnel process running in background
    if url:
        # Detach — the process stays alive
        threading.Thread(target=proc.wait, daemon=True).start()
    else:
        proc.terminate()
        console.print("  [yellow]Could not capture tunnel URL (timeout).[/yellow]")
    return url


if __name__ == "__main__":
    main()
