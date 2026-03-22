"""Dispatch assignments to workers with retries and dependency tracking."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from src.models import AgentNode, DevAssignment, Task, TaskStatus, WorkerAssignmentRequest

if TYPE_CHECKING:
    from src.metrics.collector import MetricsCollector

logger = logging.getLogger(__name__)


class Dispatcher:
    """Tracks merged agents and dispatches ready assignments."""

    def __init__(
        self,
        metrics: MetricsCollector,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._metrics = metrics
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self._merged: dict[str, set[str]] = {}
        self._waiting: dict[str, list[DevAssignment]] = {}
        self._task_repo: dict[str, str] = {}

    def set_repo(self, task_id: str, repo_name: str) -> None:
        self._task_repo[task_id] = repo_name

    async def close(self) -> None:
        await self._http.aclose()

    async def _agents_map(self) -> dict[str, AgentNode]:
        agents = await self._metrics.get_agents()
        return {a.agent_id: a for a in agents}

    async def dispatch_assignment(
        self,
        task: Task,
        assignment: DevAssignment,
        repo_name: str,
    ) -> bool:
        """POST /assignments to worker; retry on 503."""
        agents = await self._agents_map()
        node = agents.get(assignment.agent_id)
        if node is None:
            logger.error("No agent registered for %s", assignment.agent_id)
            return False
        url = f"http://{node.host}:{node.port}/assignments"
        body = WorkerAssignmentRequest(task=task, assignment=assignment, repo_name=repo_name)
        payload = body.model_dump(mode="json")
        for attempt in range(5):
            try:
                r = await self._http.post(url, json=payload)
                if r.status_code == 200:
                    await self._metrics.update_agent_status(assignment.agent_id, "busy")
                    return True
                if r.status_code == 503:
                    logger.warning("Worker busy %s, retry %s", assignment.agent_id, attempt + 1)
                    await asyncio.sleep(60)
                    continue
                logger.error("dispatch failed %s: %s %s", url, r.status_code, r.text)
                return False
            except Exception as exc:
                logger.exception("dispatch error: %s", exc)
                await asyncio.sleep(10)
        return False

    async def start_task(
        self,
        task: Task,
        plan_assignments: list[DevAssignment],
    ) -> None:
        """Initialize merge tracking and dispatch assignments with no deps."""
        tid = task.task_id
        self._merged[tid] = set()
        self._waiting[tid] = []
        repo = self._task_repo.get(tid, task.plan.repo_name if task.plan else "")
        for asg in plan_assignments:
            deps = set(asg.depends_on)
            if deps and not deps <= self._merged[tid]:
                self._waiting[tid].append(asg)
                continue
            ok = await self.dispatch_assignment(task, asg, repo)
            if not ok:
                logger.error("Initial dispatch failed for %s", asg.agent_id)
                try:
                    await self._metrics.update_task_status(tid, TaskStatus.FAILED)
                except Exception as exc:
                    logger.warning("update_task_status: %s", exc)

    async def on_agent_merged(self, task: Task, agent_id: str) -> None:
        """After PR merge, unblock dependents."""
        tid = task.task_id
        self._merged.setdefault(tid, set()).add(agent_id)
        repo = self._task_repo.get(tid, task.plan.repo_name if task.plan else "")
        wait = self._waiting.get(tid, [])
        remaining: list[DevAssignment] = []
        for asg in wait:
            deps = set(asg.depends_on)
            if deps <= self._merged[tid]:
                ok = await self.dispatch_assignment(task, asg, repo)
                if not ok:
                    remaining.append(asg)
            else:
                remaining.append(asg)
        self._waiting[tid] = remaining

    async def is_task_fully_merged(self, task: Task) -> bool:
        """True if every assignment agent has merged."""
        if not task.plan:
            return False
        needed = {a.agent_id for a in task.plan.assignments}
        done = self._merged.get(task.task_id, set())
        return needed <= done
