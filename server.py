#!/usr/bin/env python3
"""
GLOBE-CLI — FastAPI Backend with 3-Agent AI Pipeline & Real-Time Metrics
Architect → Coder → Reviewer  (Sequential, Streaming via SSE)
"""

import asyncio
import json
import os
import sys
import time
import threading
import re
import shutil
import subprocess
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
import psutil
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────
API_KEY = os.getenv("GLOBE_API_KEY", "globe-cli-secret-key-change-me")
HOST = os.getenv("GLOBE_HOST", "0.0.0.0")
PORT = int(os.getenv("GLOBE_PORT", "8787"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL_ARCHITECT = os.getenv("MODEL_ARCHITECT", "llama3.2:3b")
MODEL_CODER = os.getenv("MODEL_CODER", "qwen2.5-coder:7b")
MODEL_REVIEWER = os.getenv("MODEL_REVIEWER", "llama3.2:3b")
TUNNEL_URL = os.getenv("GLOBE_TUNNEL_URL", "")

# ─── Metrics Store ────────────────────────────────────────────────────────────
class Metrics:
    def __init__(self):
        self.total_requests: int = 0
        self.total_tokens: int = 0
        self.active_connections: int = 0
        self.start_time: float = time.time()
        self.recent_requests: deque = deque(maxlen=100)
        self._lock = threading.Lock()

    def record_request(self, ip: str):
        with self._lock:
            self.total_requests += 1
            self.recent_requests.appendleft({
                "ip": ip,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def add_tokens(self, count: int):
        with self._lock:
            self.total_tokens += count

    def connect(self):
        with self._lock:
            self.active_connections += 1

    def disconnect(self):
        with self._lock:
            self.active_connections = max(0, self.active_connections - 1)

    def snapshot(self) -> dict:
        with self._lock:
            cost_saved = round(self.total_tokens * 0.000015, 4)  # ~$0.015/1K tokens (GPT-4 output)
            return {
                "total_requests": self.total_requests,
                "total_tokens": self.total_tokens,
                "active_connections": self.active_connections,
                "uptime_seconds": round(time.time() - self.start_time, 1),
                "cost_saved_usd": cost_saved,
                "recent_requests": list(self.recent_requests)[:20],
                "cpu_percent": psutil.cpu_percent(interval=0),
                "memory_percent": psutil.virtual_memory().percent,
                "gpu_info": _get_gpu_info(),
            }

metrics = Metrics()


def _get_gpu_info() -> str:
    """Attempt to get GPU utilization via nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return "N/A"
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(", ")
            return f"{parts[0]}% util | {parts[1]}/{parts[2]} MB VRAM"
    except Exception:
        pass
    return "N/A"


# ─── Tunnel Watchdog ──────────────────────────────────────────────────────────
def _tunnel_watchdog():
    """Restart cloudflared if the tunnel URL becomes unreachable."""
    global TUNNEL_URL
    if not TUNNEL_URL or not shutil.which("cloudflared"):
        return
    while True:
        time.sleep(60)
        try:
            resp = httpx.get(TUNNEL_URL + "/health", timeout=10)
            if resp.status_code == 200:
                continue
        except Exception:
            pass
        # Tunnel is down — restart
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                match = re.search(r"(https://[a-z0-9\-]+\.trycloudflare\.com)", line)
                if match:
                    TUNNEL_URL = match.group(1)
                    break
        except Exception:
            pass


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog = threading.Thread(target=_tunnel_watchdog, daemon=True)
    watchdog.start()
    yield

app = FastAPI(
    title="GLOBE-CLI API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ─────────────────────────────────────────────────────────────────────
def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ─── Models ───────────────────────────────────────────────────────────────────
class CodeRequest(BaseModel):
    prompt: str
    context: str = ""


# ─── Ollama Streaming Helper ─────────────────────────────────────────────────
async def stream_ollama(model: str, system: str, prompt: str):
    """Yield tokens from Ollama's streaming API."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "options": {"temperature": 0.4, "num_predict": 2048},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        async with client.stream(
            "POST", f"{OLLAMA_HOST}/api/generate", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("response", "")
                if token:
                    metrics.add_tokens(1)
                    yield token
                if chunk.get("done", False):
                    return


async def collect_ollama(model: str, system: str, prompt: str) -> str:
    """Run an Ollama call and collect the full response (non-streaming)."""
    parts: list[str] = []
    async for token in stream_ollama(model, system, prompt):
        parts.append(token)
    return "".join(parts)


# ─── Agent Definitions ───────────────────────────────────────────────────────
ARCHITECT_SYSTEM = (
    "You are the Architect Agent. Analyze the user's coding request and produce a clear, "
    "numbered implementation plan. Include file structure, key functions, data flow, and "
    "technology choices. Be concise but thorough. Output ONLY the plan."
)

CODER_SYSTEM = (
    "You are the Coder Agent. You receive an implementation plan from the Architect. "
    "Write complete, production-quality code that follows the plan exactly. "
    "Include all imports, error handling, and type hints. Output ONLY code with minimal comments."
)

REVIEWER_SYSTEM = (
    "You are the Code Reviewer Agent. You receive the Architect's plan and the Coder's "
    "implementation. Audit the code for bugs, security issues, performance problems, and "
    "adherence to the plan. Provide a numbered list of findings and a final verdict: "
    "APPROVED or NEEDS_REVISION. If approved, output the final polished code."
)


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "globe-cli", "tunnel": TUNNEL_URL or None}


@app.get("/metrics")
async def get_metrics(x_api_key: str = Header(...)):
    verify_api_key(x_api_key)
    return JSONResponse(metrics.snapshot())


@app.post("/generate")
async def generate(req: CodeRequest, request: Request, x_api_key: str = Header(...)):
    """Full 3-agent pipeline with SSE streaming."""
    verify_api_key(x_api_key)
    client_ip = request.client.host if request.client else "unknown"
    metrics.record_request(client_ip)
    metrics.connect()

    async def event_stream():
        try:
            user_prompt = req.prompt
            if req.context:
                user_prompt = f"Context:\n{req.context}\n\nRequest:\n{req.prompt}"

            # ── Phase 1: Architect ──
            yield {"event": "agent", "data": json.dumps({"agent": "architect", "status": "start"})}
            plan = ""
            async for token in stream_ollama(MODEL_ARCHITECT, ARCHITECT_SYSTEM, user_prompt):
                plan += token
                yield {"event": "token", "data": json.dumps({"agent": "architect", "token": token})}
            yield {"event": "agent", "data": json.dumps({"agent": "architect", "status": "done"})}

            # ── Phase 2: Coder ──
            yield {"event": "agent", "data": json.dumps({"agent": "coder", "status": "start"})}
            coder_prompt = f"## Architect's Plan:\n{plan}\n\n## Original Request:\n{user_prompt}"
            code = ""
            async for token in stream_ollama(MODEL_CODER, CODER_SYSTEM, coder_prompt):
                code += token
                yield {"event": "token", "data": json.dumps({"agent": "coder", "token": token})}
            yield {"event": "agent", "data": json.dumps({"agent": "coder", "status": "done"})}

            # ── Phase 3: Reviewer ──
            yield {"event": "agent", "data": json.dumps({"agent": "reviewer", "status": "start"})}
            reviewer_prompt = (
                f"## Architect's Plan:\n{plan}\n\n"
                f"## Coder's Implementation:\n{code}\n\n"
                f"## Original Request:\n{user_prompt}"
            )
            async for token in stream_ollama(MODEL_REVIEWER, REVIEWER_SYSTEM, reviewer_prompt):
                yield {"event": "token", "data": json.dumps({"agent": "reviewer", "token": token})}
            yield {"event": "agent", "data": json.dumps({"agent": "reviewer", "status": "done"})}

            yield {"event": "done", "data": json.dumps({"status": "complete"})}

        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
        finally:
            metrics.disconnect()

    return EventSourceResponse(event_stream())


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(x_api_key: str | None = None):
    """Lightweight HTML dashboard for browser access."""
    return HTMLResponse(content=DASHBOARD_HTML)


# ─── Embedded Dashboard HTML ─────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GLOBE-CLI Dashboard</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#0a0e17;color:#c8d6e5;font-family:'Courier New',monospace;padding:20px}
  h1{color:#00d4ff;text-align:center;margin-bottom:20px;text-shadow:0 0 20px #00d4ff55}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:20px}
  .card{background:#111827;border:1px solid #1e3a5f;border-radius:12px;padding:20px}
  .card h3{color:#00d4ff;margin-bottom:12px;font-size:14px;text-transform:uppercase;letter-spacing:2px}
  .stat{font-size:32px;font-weight:bold;color:#00ff88}
  .stat.warn{color:#ffaa00}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:8px;border-bottom:1px solid #1e3a5f;font-size:13px}
  th{color:#00d4ff}
  .pulse{animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
</style>
</head>
<body>
<h1>🌐 GLOBE-CLI — Live Dashboard</h1>
<div class="grid" id="cards"></div>
<div class="card"><h3>Recent Global Requests</h3><table>
<thead><tr><th>IP Address</th><th>Timestamp (UTC)</th></tr></thead>
<tbody id="req-table"></tbody>
</table></div>
<script>
async function refresh(){
  try{
    const r=await fetch('/metrics',{headers:{'X-API-KEY':new URLSearchParams(location.search).get('key')||''}});
    const d=await r.json();
    document.getElementById('cards').innerHTML=`
      <div class="card"><h3>Total Requests</h3><div class="stat">${d.total_requests}</div></div>
      <div class="card"><h3>Total Tokens</h3><div class="stat">${d.total_tokens.toLocaleString()}</div></div>
      <div class="card"><h3>Active Connections</h3><div class="stat pulse">${d.active_connections}</div></div>
      <div class="card"><h3>Cost Saved (USD)</h3><div class="stat">$${d.cost_saved_usd.toFixed(4)}</div></div>
      <div class="card"><h3>CPU Load</h3><div class="stat ${d.cpu_percent>80?'warn':''}">${d.cpu_percent}%</div></div>
      <div class="card"><h3>Memory</h3><div class="stat ${d.memory_percent>85?'warn':''}">${d.memory_percent}%</div></div>
      <div class="card"><h3>GPU</h3><div class="stat" style="font-size:16px">${d.gpu_info}</div></div>
      <div class="card"><h3>Uptime</h3><div class="stat" style="font-size:20px">${Math.floor(d.uptime_seconds/3600)}h ${Math.floor((d.uptime_seconds%3600)/60)}m</div></div>`;
    const rows=d.recent_requests.map(r=>`<tr><td>${r.ip}</td><td>${r.timestamp}</td></tr>`).join('');
    document.getElementById('req-table').innerHTML=rows||'<tr><td colspan="2">No requests yet</td></tr>';
  }catch(e){console.error(e)}
}
refresh();setInterval(refresh,3000);
</script>
</body>
</html>"""


# ─── Startup Banner ──────────────────────────────────────────────────────────
def print_banner():
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console(force_terminal=True)
        console.print(Panel(
            f"[bold cyan]🌐 GLOBE-CLI Server[/bold cyan]\n\n"
            f"  [white]Local:[/white]     http://localhost:{PORT}\n"
            f"  [white]Tunnel:[/white]    {TUNNEL_URL or 'N/A'}\n"
            f"  [white]Dashboard:[/white] http://localhost:{PORT}/dashboard?key={API_KEY[:16]}...\n"
            f"  [white]Models:[/white]    {MODEL_ARCHITECT} / {MODEL_CODER} / {MODEL_REVIEWER}\n"
            f"  [white]API Key:[/white]   {API_KEY[:16]}...",
            title="🌐 GLOBE-CLI Server",
            border_style="bold cyan",
        ))
    except ImportError:
        print(f"GLOBE-CLI running on http://localhost:{PORT}")


if __name__ == "__main__":
    print_banner()
    uvicorn.run(
        "server:app",
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
    )
