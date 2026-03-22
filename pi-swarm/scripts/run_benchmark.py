#!/usr/bin/env python3
"""
Benchmark suite: submits tasks and records wall time / status.

Set ORCHESTRATOR_URL or use --orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BENCHMARK_TASKS: list[dict[str, str]] = [
    {"title": "FizzBuzz", "description": "Print FizzBuzz 1..30.", "repo": "bench-fizz"},
    {
        "title": "Calculator CLI",
        "description": "Single-file argparse calculator: add sub mul div.",
        "repo": "bench-calc",
    },
    {
        "title": "URL shortener",
        "description": "FastAPI app with in-memory URL map and redirect.",
        "repo": "bench-short",
    },
]


async def wait_task(client: httpx.AsyncClient, base: str, task_id: str, timeout: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        r = await client.get(f"{base}/tasks/{task_id}")
        if r.status_code != 200:
            await asyncio.sleep(5)
            continue
        data = r.json()
        st = data.get("status")
        if st in ("completed", "failed"):
            return {
                "task_id": task_id,
                "status": st,
                "wall_seconds": time.perf_counter() - t0,
            }
        await asyncio.sleep(10)
    return {"task_id": task_id, "status": "timeout", "wall_seconds": timeout}


async def run_benchmark(base: str, timeout: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for spec in BENCHMARK_TASKS:
            r = await client.post(
                f"{base}/tasks",
                json={
                    "title": spec["title"],
                    "description": spec["description"],
                    "language": "python",
                    "repo_name": spec["repo"],
                },
            )
            if r.status_code >= 400:
                out.append({"error": r.text, "spec": spec})
                continue
            tid = r.json()["task_id"]
            res = await wait_task(client, base, tid, timeout)
            out.append({"spec": spec, **res})
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--orchestrator",
        default=os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8080"),
    )
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument("--out-json", default="benchmark_results.json")
    p.add_argument("--out-md", default="benchmark_report.md")
    args = p.parse_args()
    base = args.orchestrator.rstrip("/")

    async def inner() -> list[dict[str, Any]]:
        return await run_benchmark(base, args.timeout)

    try:
        results = asyncio.run(inner())
    except Exception as exc:
        logger.exception("benchmark failed: %s", exc)
        return 1
    Path(args.out_json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = ["| Task | Status | Wall (s) |", "| --- | --- | --- |"]
    for row in results:
        if "spec" in row:
            t = row["spec"].get("title", "")
            lines.append(f"| {t} | {row.get('status', '')} | {row.get('wall_seconds', ''):.1f} |")
    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out_json} and {args.out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
