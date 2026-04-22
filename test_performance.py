#!/usr/bin/env python3
"""
GLOBE-CLI — Performance & Load Test Suite
Tests endpoint health, latency, streaming, and concurrent load.
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

console = Console(force_terminal=True)

API_KEY = os.getenv("GLOBE_API_KEY", "")
BASE_URL = os.getenv("GLOBE_TUNNEL_URL") or f"http://localhost:{os.getenv('GLOBE_PORT', '8787')}"
HEADERS = {"X-API-KEY": API_KEY}


@dataclass
class TestResult:
    name: str
    passed: bool
    latency_ms: float = 0.0
    detail: str = ""
    tokens: int = 0


@dataclass
class LoadResult:
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    total_tokens: int = 0
    elapsed_s: float = 0.0
    latencies: list[float] = field(default_factory=list)


async def test_health() -> TestResult:
    """Test the /health endpoint."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{BASE_URL}/health")
            latency = (time.perf_counter() - start) * 1000
            data = resp.json()
            return TestResult(
                name="Health Check",
                passed=resp.status_code == 200 and data.get("status") == "ok",
                latency_ms=latency,
                detail=json.dumps(data),
            )
    except Exception as e:
        return TestResult(name="Health Check", passed=False, detail=str(e))


async def test_auth_reject() -> TestResult:
    """Test that invalid API keys are rejected."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BASE_URL}/generate",
                json={"prompt": "test"},
                headers={"X-API-KEY": "wrong-key"},
            )
            latency = (time.perf_counter() - start) * 1000
            return TestResult(
                name="Auth Rejection",
                passed=resp.status_code == 403,
                latency_ms=latency,
                detail=f"Status: {resp.status_code}",
            )
    except Exception as e:
        return TestResult(name="Auth Rejection", passed=False, detail=str(e))


async def test_metrics() -> TestResult:
    """Test the /metrics endpoint."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{BASE_URL}/metrics", headers=HEADERS)
            latency = (time.perf_counter() - start) * 1000
            data = resp.json()
            has_keys = all(k in data for k in ["total_requests", "total_tokens", "cpu_percent"])
            return TestResult(
                name="Metrics Endpoint",
                passed=resp.status_code == 200 and has_keys,
                latency_ms=latency,
                detail=f"Requests: {data.get('total_requests')}, Tokens: {data.get('total_tokens')}",
            )
    except Exception as e:
        return TestResult(name="Metrics Endpoint", passed=False, detail=str(e))


async def test_streaming() -> TestResult:
    """Test a single streaming /generate request through the full pipeline."""
    start = time.perf_counter()
    token_count = 0
    agents_seen = set()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{BASE_URL}/generate",
                json={"prompt": "Write a Python hello world function"},
                headers={**HEADERS, "Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    return TestResult(
                        name="Streaming Pipeline",
                        passed=False,
                        detail=f"Status: {resp.status_code}",
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if "token" in data:
                            token_count += 1
                        if "agent" in data:
                            agents_seen.add(data["agent"])

        latency = (time.perf_counter() - start) * 1000
        all_agents = {"architect", "coder", "reviewer"}.issubset(agents_seen)
        return TestResult(
            name="Streaming Pipeline",
            passed=all_agents and token_count > 0,
            latency_ms=latency,
            tokens=token_count,
            detail=f"Agents: {agents_seen}, Tokens: {token_count}",
        )
    except Exception as e:
        return TestResult(name="Streaming Pipeline", passed=False, detail=str(e))


async def _single_request(client: httpx.AsyncClient, prompt: str) -> tuple[float, int, bool]:
    """Run a single request and return (latency_ms, tokens, success)."""
    start = time.perf_counter()
    tokens = 0
    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/generate",
            json={"prompt": prompt},
            headers={**HEADERS, "Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code != 200:
                return ((time.perf_counter() - start) * 1000, 0, False)
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if "token" in data:
                        tokens += 1
        return ((time.perf_counter() - start) * 1000, tokens, True)
    except Exception:
        return ((time.perf_counter() - start) * 1000, 0, False)


async def load_test(concurrency: int = 3, total: int = 5) -> LoadResult:
    """Run concurrent load test."""
    result = LoadResult(total_requests=total)
    prompts = [
        "Write a bubble sort in Python",
        "Create a REST API health endpoint",
        "Write a fibonacci function",
        "Create a simple calculator class",
        "Write a linked list in Python",
    ]
    sem = asyncio.Semaphore(concurrency)
    start = time.perf_counter()

    async def bounded(i: int):
        async with sem:
            async with httpx.AsyncClient(timeout=300.0) as client:
                return await _single_request(client, prompts[i % len(prompts)])

    tasks = [bounded(i) for i in range(total)]
    results = await asyncio.gather(*tasks)

    for latency, tokens, success in results:
        result.latencies.append(latency)
        result.total_tokens += tokens
        if success:
            result.successful += 1
        else:
            result.failed += 1

    result.elapsed_s = time.perf_counter() - start
    result.latencies.sort()
    if result.latencies:
        result.avg_latency_ms = sum(result.latencies) / len(result.latencies)
        idx = int(len(result.latencies) * 0.95)
        result.p95_latency_ms = result.latencies[min(idx, len(result.latencies) - 1)]

    return result


async def run_all():
    console.print(Panel(
        "[bold cyan]🌐 GLOBE-CLI Performance Test Suite[/bold cyan]",
        border_style="cyan",
    ))

    # ── Unit tests ──
    console.print("\n[bold]Running endpoint tests...[/bold]\n")
    tests = await asyncio.gather(
        test_health(),
        test_auth_reject(),
        test_metrics(),
    )

    table = Table(title="Endpoint Tests", border_style="cyan", header_style="bold cyan")
    table.add_column("Test", min_width=20)
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Detail")

    for t in tests:
        status = "[bold green]PASS[/bold green]" if t.passed else "[bold red]FAIL[/bold red]"
        table.add_row(t.name, status, f"{t.latency_ms:.0f}ms", t.detail[:60])

    console.print(table)

    # ── Streaming test ──
    console.print("\n[bold]Running streaming pipeline test (this takes a while)...[/bold]\n")
    stream_result = await test_streaming()
    status = "[bold green]PASS[/bold green]" if stream_result.passed else "[bold red]FAIL[/bold red]"
    console.print(Panel(
        f"  Status:  {status}\n"
        f"  Tokens:  {stream_result.tokens}\n"
        f"  Latency: {stream_result.latency_ms / 1000:.1f}s\n"
        f"  Detail:  {stream_result.detail}",
        title="🔄 Streaming Pipeline",
        border_style="cyan",
    ))

    # ── Load test ──
    console.print("\n[bold]Running load test (3 concurrent × 5 requests)...[/bold]\n")
    load = await load_test(concurrency=3, total=5)

    load_table = Table(title="Load Test Results", border_style="cyan", header_style="bold cyan")
    load_table.add_column("Metric", min_width=20)
    load_table.add_column("Value", justify="right")
    load_table.add_row("Total Requests", str(load.total_requests))
    load_table.add_row("Successful", f"[green]{load.successful}[/green]")
    load_table.add_row("Failed", f"[red]{load.failed}[/red]")
    load_table.add_row("Avg Latency", f"{load.avg_latency_ms / 1000:.1f}s")
    load_table.add_row("P95 Latency", f"{load.p95_latency_ms / 1000:.1f}s")
    load_table.add_row("Total Tokens", f"{load.total_tokens:,}")
    load_table.add_row("Total Time", f"{load.elapsed_s:.1f}s")
    load_table.add_row("Throughput", f"{load.successful / load.elapsed_s:.2f} req/s")
    console.print(load_table)

    # ── Summary ──
    passed = sum(1 for t in tests if t.passed) + (1 if stream_result.passed else 0)
    total = len(tests) + 1
    color = "green" if passed == total else "yellow"
    console.print(Panel(
        f"[bold {color}]{passed}/{total} tests passed[/bold {color}]  •  "
        f"Load: {load.successful}/{load.total_requests} succeeded",
        title="📋 Summary",
        border_style=color,
    ))


if __name__ == "__main__":
    asyncio.run(run_all())
