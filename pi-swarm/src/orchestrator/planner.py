"""Task decomposition via lead LLM with validation and fallback."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from src.llm import SwarmLLM, truncate_to_fit
from src.models import AgentNode, AgentRole, DevAssignment, Task, TaskPlan

if TYPE_CHECKING:
    from src.metrics.collector import MetricsCollector

logger = logging.getLogger(__name__)


def _topo_sort(assignments: list[DevAssignment]) -> list[DevAssignment]:
    """Order assignments so dependencies come first."""
    ids = {a.agent_id for a in assignments}
    pending = {a.agent_id: set(a.depends_on) & ids for a in assignments}
    order: list[DevAssignment] = []
    by_id = {a.agent_id: a for a in assignments}
    ready = [aid for aid, deps in pending.items() if not deps]
    while ready:
        n = ready.pop()
        order.append(by_id[n])
        for aid, deps in pending.items():
            if n in deps:
                deps.discard(n)
                if not deps and aid not in {x.agent_id for x in order}:
                    ready.append(aid)
    if len(order) != len(assignments):
        logger.warning("Topological sort incomplete; using stable order")
        return assignments
    return order


def _has_cycle(assignments: list[DevAssignment]) -> bool:
    ids = {a.agent_id for a in assignments}
    graph: dict[str, list[str]] = {a.agent_id: [d for d in a.depends_on if d in ids] for a in assignments}
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(n: str) -> bool:
        if n in stack:
            return True
        if n in visited:
            return False
        visited.add(n)
        stack.add(n)
        for m in graph.get(n, []):
            if dfs(m):
                return True
        stack.discard(n)
        return False

    for node in ids:
        if node not in visited and dfs(node):
            return True
    return False


def validate_plan(plan: TaskPlan, agents: list[AgentNode]) -> tuple[bool, str]:
    """Validate plan structure."""
    agent_ids = {a.agent_id for a in agents if a.role == AgentRole.DEVELOPER}
    if not plan.assignments:
        return False, "no assignments"
    seen_branches: set[str] = set()
    for asg in plan.assignments:
        if asg.agent_id not in agent_ids:
            return False, f"unknown agent {asg.agent_id}"
        if not asg.files_to_create and not asg.files_to_modify:
            return False, f"no files for {asg.agent_id}"
        if asg.branch_name in seen_branches:
            return False, f"duplicate branch {asg.branch_name}"
        seen_branches.add(asg.branch_name)
    if _has_cycle(plan.assignments):
        return False, "circular dependency"
    return True, ""


def fallback_single_agent_plan(task: Task, repo_name: str, agent_id: str) -> TaskPlan:
    """Single assignment covering the whole task."""
    branch = f"{task.task_id}/{agent_id}/implementation"
    return TaskPlan(
        task_id=task.task_id,
        summary="Fallback single-agent plan (LLM validation failed).",
        repo_name=repo_name,
        shared_interfaces="# Shared: follow task description and PEP8.",
        assignments=[
            DevAssignment(
                agent_id=agent_id,
                description=task.description,
                files_to_create=["main.py"],
                files_to_modify=[],
                branch_name=branch,
                depends_on=[],
                acceptance_criteria=["Code runs without syntax errors."],
            ),
        ],
    )


async def build_plan(
    task: Task,
    agents: list[AgentNode],
    file_list: list[str],
    llm: SwarmLLM,
    planner_prompt_template: str,
    metrics: MetricsCollector | None,
    repo_name: str,
) -> TaskPlan:
    """Produce TaskPlan via LLM; validate; fallback on failure."""
    dev_agents = [a for a in agents if a.role.value == "developer"]
    pick = dev_agents[0].agent_id if dev_agents else "dev-01"
    schema = {
        "task_id": task.task_id,
        "summary": "string",
        "repo_name": repo_name,
        "shared_interfaces": "string",
        "assignments": [
            {
                "agent_id": "dev-01",
                "description": "string",
                "files_to_create": ["path/file.py"],
                "files_to_modify": [],
                "branch_name": f"{task.task_id}/dev-01/feature",
                "depends_on": [],
                "acceptance_criteria": ["criterion"],
            },
        ],
    }
    agents_json = json.dumps([a.model_dump() for a in dev_agents], default=str)
    files_str = truncate_to_fit("\n".join(file_list) if file_list else "(empty repo)")
    user = planner_prompt_template.format(
        agent_list_json=agents_json,
        file_list=files_str,
        task_id=task.task_id,
        repo_name=repo_name,
        schema_json=json.dumps(schema),
    )
    system = "You output only JSON for the development plan."
    t0 = time.perf_counter()
    data, tin, tout = await __import__("asyncio").to_thread(
        llm.generate_json,
        system,
        f"Task title: {task.title}\n\nTask description:\n{task.description}\n\n{user}",
        json.dumps(schema),
    )
    dur = time.perf_counter() - t0
    if metrics:
        from src.models import MetricsRecord

        await metrics.log_event(
            MetricsRecord(
                task_id=task.task_id,
                agent_id="lead",
                event="planner_llm",
                tokens_in=tin,
                tokens_out=tout,
                duration_seconds=dur,
                metadata={"phase": "plan"},
            ),
        )
    plan: TaskPlan | None = None
    try:
        if data:
            data["task_id"] = task.task_id
            data["repo_name"] = str(data.get("repo_name", repo_name))
            asgs = []
            for raw in data.get("assignments", []) or []:
                asgs.append(DevAssignment.model_validate(raw))
            plan = TaskPlan(
                task_id=task.task_id,
                summary=str(data.get("summary", "")),
                repo_name=str(data.get("repo_name", repo_name)),
                shared_interfaces=str(data.get("shared_interfaces", "")),
                assignments=_topo_sort(asgs),
            )
    except Exception as exc:
        logger.exception("Plan parse failed: %s", exc)
        plan = None
    if plan:
        ok, reason = validate_plan(plan, agents)
        if ok:
            return plan
        logger.warning("Plan validation failed: %s — retrying once", reason)
        data2, _, _ = await __import__("asyncio").to_thread(
            llm.generate_json,
            system,
            user + "\nFix: each assignment must reference valid agent_id and files.",
            json.dumps(schema),
        )
        try:
            if data2:
                data2["task_id"] = task.task_id
                data2["repo_name"] = str(data2.get("repo_name", repo_name))
                asgs2 = [DevAssignment.model_validate(x) for x in data2.get("assignments", []) or []]
                plan2 = TaskPlan(
                    task_id=task.task_id,
                    summary=str(data2.get("summary", "")),
                    repo_name=str(data2.get("repo_name", repo_name)),
                    shared_interfaces=str(data2.get("shared_interfaces", "")),
                    assignments=_topo_sort(asgs2),
                )
                ok2, _ = validate_plan(plan2, agents)
                if ok2:
                    return plan2
        except Exception as exc:
            logger.warning("Second plan attempt failed: %s", exc)
    logger.warning("Using fallback single-agent plan")
    return fallback_single_agent_plan(task, repo_name, pick)
