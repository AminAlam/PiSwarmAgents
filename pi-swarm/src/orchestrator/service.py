"""Orchestrator background pipelines: planning, review, merge."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.config import read_prompt_file
from src.git_ops import GiteaClient
from src.llm import SwarmLLM
from src.metrics.collector import MetricsCollector
from src.models import AgentNode, AgentRole, AssignmentResult, DevAssignment, Task, TaskStatus
from src.orchestrator.dispatcher import Dispatcher
from src.orchestrator.merger import try_merge_pr
from src.orchestrator.planner import build_plan
from src.orchestrator.reviewer import review_pr

if TYPE_CHECKING:
    from src.config import GiteaConfig, LLMConfig, OrchestratorConfig, SwarmYamlConfig

logger = logging.getLogger(__name__)


async def ensure_repo_and_webhook(
    gitea: GiteaClient,
    repo_name: str,
    webhook_base: str,
) -> None:
    """Create repo if missing; attach webhook."""
    try:
        if not await gitea.repo_exists(repo_name):
            await gitea.create_repo(repo_name, description=f"Pi Swarm — {repo_name}")
        target = f"{webhook_base.rstrip('/')}/webhooks/gitea"
        await gitea.setup_webhook(repo_name, target)
    except Exception as exc:
        logger.exception("ensure_repo_and_webhook: %s", exc)


def _find_assignment(task: Task, agent_id: str) -> DevAssignment | None:
    if not task.plan:
        return None
    for a in task.plan.assignments:
        if a.agent_id == agent_id:
            return a
    return None


async def run_planning_pipeline(
    task_id: str,
    metrics: MetricsCollector,
    gitea: GiteaClient,
    llm: SwarmLLM,
    orch: OrchestratorConfig,
    yaml_cfg: SwarmYamlConfig,
    dispatcher: Dispatcher,
) -> None:
    """Plan task and dispatch work."""
    task = await metrics.get_task(task_id)
    if not task:
        return
    try:
        await metrics.update_task_status(task_id, TaskStatus.PLANNING)
        agents = [a for a in await metrics.get_agents() if a.role == AgentRole.DEVELOPER]
        if not agents:
            logger.warning("No developer agents registered; planning anyway")
        raw = (task.repo_name or task.title or f"task-{task_id}").strip()
        repo_name = re.sub(r"[^a-z0-9._-]", "-", raw.lower())[:60].strip("-") or f"task-{task_id}"
        tpl = read_prompt_file(orch.config_path, "lead_planner")
        files = await gitea.list_files(repo_name) if await gitea.repo_exists(repo_name) else []
        await ensure_repo_and_webhook(gitea, repo_name, yaml_cfg.webhook_base_url)
        plan = await build_plan(task, agents, files, llm, tpl, metrics, repo_name)
        task.plan = plan
        task.repo_url = f"{gitea.base_url}/{gitea.organization}/{repo_name}"
        task.status = TaskStatus.IN_PROGRESS
        await metrics.save_task(task)
        dispatcher.set_repo(task_id, plan.repo_name)
        await dispatcher.start_task(task, plan.assignments)
    except Exception as exc:
        logger.exception("planning failed: %s", exc)
        await metrics.update_task_status(task_id, TaskStatus.FAILED)


async def run_review_merge(
    task: Task,
    repo: str,
    pr_number: int,
    assignment: DevAssignment,
    gitea: GiteaClient,
    llm: SwarmLLM,
    orch: OrchestratorConfig,
    yaml_cfg: SwarmYamlConfig,
    metrics: MetricsCollector,
    dispatcher: Dispatcher,
    review_key: str,
    review_counts: dict[str, int],
) -> None:
    """Review PR; merge if approved."""
    try:
        max_r = orch.max_review_rounds
        review_counts[review_key] = review_counts.get(review_key, 0) + 1
        if review_counts[review_key] > max_r:
            logger.error("Max review rounds for %s", review_key)
            await metrics.update_task_status(task.task_id, TaskStatus.FAILED)
            return
        rtpl = read_prompt_file(orch.config_path, "lead_reviewer")
        rev = await review_pr(
            gitea,
            repo,
            pr_number,
            assignment,
            task.task_id,
            llm,
            rtpl,
            metrics,
        )
        if rev.approved:
            await try_merge_pr(
                gitea,
                repo,
                pr_number,
                assignment,
                task,
                llm,
                metrics,
                orch.auto_merge_on_approval,
                dispatcher.on_agent_merged,
                dispatcher,
            )
        else:
            body = "\n".join(rev.comments) or "Needs revision"
            await gitea.add_pr_comment(repo, pr_number, body)
    except Exception as exc:
        logger.exception("run_review_merge: %s", exc)


async def handle_worker_result(
    result: AssignmentResult,
    metrics: MetricsCollector,
    gitea: GiteaClient,
    llm: SwarmLLM,
    orch: OrchestratorConfig,
    yaml_cfg: SwarmYamlConfig,
    dispatcher: Dispatcher,
    review_counts: dict[str, int],
) -> None:
    """After worker reports PR, run review/merge."""
    task = await metrics.get_task(result.task_id)
    if not task:
        return
    repo = task.plan.repo_name if task.plan else ""
    if not result.success or not result.pr_number or not repo:
        await metrics.update_task_status(result.task_id, TaskStatus.FAILED)
        return
    asg = _find_assignment(task, result.agent_id)
    if not asg:
        return
    rk = f"{task.task_id}:{result.pr_number}"
    await run_review_merge(
        task,
        repo,
        result.pr_number,
        asg,
        gitea,
        llm,
        orch,
        yaml_cfg,
        metrics,
        dispatcher,
        rk,
        review_counts,
    )
