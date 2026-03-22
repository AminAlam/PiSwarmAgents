"""Merge approved PRs; optional LLM conflict resolution."""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from src.git_ops import GiteaClient
from src.llm import SwarmLLM, truncate_to_fit
from src.metrics.collector import MetricsCollector
from src.models import DevAssignment, Task, TaskStatus
from src.orchestrator.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


async def try_merge_pr(
    gitea: GiteaClient,
    repo: str,
    pr_number: int,
    assignment: DevAssignment,
    task: Task,
    llm: SwarmLLM | None,
    metrics: MetricsCollector | None,
    orchestrator_config_auto_merge: bool,
    on_merged: Callable[[Task, str], Awaitable[None]],
    dispatcher: Dispatcher | None = None,
) -> bool:
    """Merge PR; resolve conflicts with LLM if needed."""
    pr = await gitea.get_pr(repo, pr_number)
    if not pr:
        return False
    mergeable = pr.get("mergeable", True)
    if orchestrator_config_auto_merge is False:
        return False
    if mergeable is False and llm:
        await _llm_resolve_and_push(gitea, repo, pr_number, assignment, task, llm, metrics)
        pr = await gitea.get_pr(repo, pr_number)
        mergeable = pr.get("mergeable", True)
    if mergeable is False:
        logger.error("PR %s still not mergeable", pr_number)
        return False
    t0 = time.perf_counter()
    res = await gitea.merge_pr(repo, pr_number)
    dur = time.perf_counter() - t0
    if res.get("error"):
        logger.error("merge_pr error: %s", res.get("error"))
        return False
    if metrics:
        from src.models import MetricsRecord

        await metrics.log_event(
            MetricsRecord(
                task_id=task.task_id,
                agent_id="lead",
                event="merge",
                duration_seconds=dur,
                metadata={"pr": str(pr_number)},
            ),
        )
    await on_merged(task, assignment.agent_id)
    if dispatcher and metrics and await dispatcher.is_task_fully_merged(task):
        await metrics.update_task_status(task.task_id, TaskStatus.COMPLETED)
    return True


async def _llm_resolve_and_push(
    gitea: GiteaClient,
    repo: str,
    pr_number: int,
    assignment: DevAssignment,
    task: Task,
    llm: SwarmLLM,
    metrics: MetricsCollector | None,
) -> None:
    """Attempt one-shot LLM merge for conflicting files."""
    diff = await gitea.get_pr_diff(repo, pr_number)
    diff = truncate_to_fit(diff, 8000)
    paths = list(set(assignment.files_to_modify + assignment.files_to_create))[:5]
    contents = ""
    for p in paths:
        c = await gitea.get_file_content(repo, p, branch="main")
        contents += f"\n--- {p} (main) ---\n{c}\n"
    system = "You merge conflicting changes into a single valid Python file."
    user = f"Assignment:\n{assignment.description}\n\nDiff:\n{diff}\n\nMain files:\n{contents}\nOutput only final merged file body for primary path."
    t0 = time.perf_counter()
    text, tin, tout = await __import__("asyncio").to_thread(
        llm.generate,
        system,
        user,
        max_tokens=2048,
    )
    dur = time.perf_counter() - t0
    if metrics:
        from src.models import MetricsRecord

        await metrics.log_event(
            MetricsRecord(
                task_id=task.task_id,
                agent_id="lead",
                event="merger_llm",
                tokens_in=tin,
                tokens_out=tout,
                duration_seconds=dur,
            ),
        )
    pr_data = await gitea.get_pr(repo, pr_number)
    head_obj = pr_data.get("head") if isinstance(pr_data.get("head"), dict) else {}
    head = str(head_obj.get("ref", ""))
    if not head or not paths:
        return
    primary = paths[0]
    try:
        await gitea.create_or_update_file(
            repo,
            head,
            primary,
            text.strip() or "# merge fallback",
            "chore: LLM merge resolution",
        )
    except Exception as exc:
        logger.exception("LLM merge push failed: %s", exc)


async def check_task_complete(task: Task, gitea: GiteaClient, metrics: MetricsCollector) -> None:
    """If no open PRs for repo, mark completed (simplified heuristic)."""
    if not task.plan:
        return
    repo = task.plan.repo_name
    try:
        open_prs = await gitea.list_open_prs(repo)
        if not open_prs:
            await metrics.update_task_status(task.task_id, TaskStatus.COMPLETED)
    except Exception as exc:
        logger.warning("check_task_complete: %s", exc)
