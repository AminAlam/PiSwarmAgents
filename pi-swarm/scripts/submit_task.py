#!/usr/bin/env python3
"""
CLI to submit a task to the orchestrator.

Usage:
  PYTHONPATH=. python scripts/submit_task.py \\
    --orchestrator http://127.0.0.1:8080 \\
    --title "Build a URL shortener" \\
    --description "Create a Python FastAPI app with..." \\
    --language python \\
    --repo url-shortener

  python scripts/submit_task.py ... --plan-file plan.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="Submit a Pi Swarm task")
    p.add_argument("--orchestrator", default="http://127.0.0.1:8080", help="Orchestrator base URL")
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--language", default="python")
    p.add_argument("--repo", dest="repo_name", required=True)
    p.add_argument("--plan-file", default="", help="Optional TaskPlan JSON (manual plan)")
    args = p.parse_args()
    base = args.orchestrator.rstrip("/")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if args.plan_file:
                plan_path = Path(args.plan_file)
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                r0 = await client.post(
                    f"{base}/tasks",
                    json={
                        "title": args.title,
                        "description": args.description,
                        "language": args.language,
                        "repo_name": args.repo_name,
                    },
                )
                r0.raise_for_status()
                tid = r0.json()["task_id"]
                plan["task_id"] = tid
                r1 = await client.post(f"{base}/tasks/{tid}/manual", json=plan)
                r1.raise_for_status()
                print(f"task_id={tid}")
            else:
                r = await client.post(
                    f"{base}/tasks",
                    json={
                        "title": args.title,
                        "description": args.description,
                        "language": args.language,
                        "repo_name": args.repo_name,
                    },
                )
                r.raise_for_status()
                tid = r.json()["task_id"]
                print(f"task_id={tid}")
            print(f"dashboard={base}/dashboard")

    try:
        asyncio.run(run())
    except Exception as exc:
        logger.exception("submit failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
