"""Assignment execution: LLM code generation + Gitea PR."""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from src.config import read_prompt_file
from src.git_ops import GiteaClient
from src.llm import SwarmLLM
from src.models import AssignmentResult, DevAssignment, Task
from src.worker.executor import check_python_syntax

if TYPE_CHECKING:
    from src.metrics.collector import MetricsCollector

logger = logging.getLogger(__name__)


def parse_file_blocks(llm_output: str) -> dict[str, str]:
    """Parse FILE: markers from LLM output."""
    pattern = r"FILE:\s*(.+?)\s*\n```\w*\n(.*?)```"
    matches = re.findall(pattern, llm_output, re.DOTALL)
    return {path.strip(): content for path, content in matches}


async def execute_assignment(
    assignment: DevAssignment,
    task: Task,
    llm: SwarmLLM,
    gitea: GiteaClient,
    repo_name: str,
    config_path: str,
    metrics: MetricsCollector | None,
) -> AssignmentResult:
    """Generate code, validate, push branch, open PR."""
    if not task.plan:
        return AssignmentResult(
            agent_id=assignment.agent_id,
            task_id=task.task_id,
            branch_name=assignment.branch_name,
            success=False,
            error_message="Task has no plan",
        )
    prompt_tmpl = read_prompt_file(config_path, "dev_coder")
    existing: dict[str, str] = {}
    for rel in assignment.files_to_modify:
        try:
            existing[rel] = await gitea.get_file_content(repo_name, rel, branch="main")
        except Exception as exc:
            logger.warning("Could not read %s: %s", rel, exc)
            existing[rel] = ""
    existing_blob = "\n\n".join(f"--- {k} ---\n{v}" for k, v in existing.items())
    files_out: dict[str, str] = {}
    syntax_errors: list[str] = []
    for attempt in range(3):
        user = prompt_tmpl.format(
            description=assignment.description,
            shared_interfaces=task.plan.shared_interfaces,
            existing_files=existing_blob or "(none)",
            language=task.language,
            constraints=", ".join(task.constraints),
        )
        if attempt:
            user += "\n\nRemember: output ONLY FILE: blocks with fenced code."
        if syntax_errors:
            user += f"\n\nFix syntax errors:\n" + "\n".join(syntax_errors)
        t0 = time.perf_counter()
        text, tin, tout = await __import__("asyncio").to_thread(
            llm.generate,
            "You are a careful Python developer.",
            user,
            max_tokens=4096,
        )
        dur = time.perf_counter() - t0
        if metrics:
            from src.models import MetricsRecord

            await metrics.log_event(
                MetricsRecord(
                    task_id=task.task_id,
                    agent_id=assignment.agent_id,
                    event="coder_llm",
                    tokens_in=tin,
                    tokens_out=tout,
                    duration_seconds=dur,
                    metadata={"attempt": str(attempt)},
                ),
            )
        files_out = parse_file_blocks(text)
        if not files_out:
            continue
        syntax_errors = check_python_syntax(files_out)
        if not syntax_errors:
            break
    if not files_out:
        return AssignmentResult(
            agent_id=assignment.agent_id,
            task_id=task.task_id,
            branch_name=assignment.branch_name,
            success=False,
            error_message="LLM produced no parseable FILE blocks",
        )
    try:
        await gitea.create_branch(repo_name, assignment.branch_name, "main")
    except Exception as exc:
        logger.exception("create_branch: %s", exc)
        return AssignmentResult(
            agent_id=assignment.agent_id,
            task_id=task.task_id,
            branch_name=assignment.branch_name,
            success=False,
            error_message=str(exc),
        )
    try:
        await gitea.push_files(
            repo_name,
            assignment.branch_name,
            files_out,
            f"feat: {assignment.branch_name}",
        )
    except Exception as exc:
        logger.exception("push_files: %s", exc)
    pr_number: int | None = None
    try:
        pr = await gitea.create_pr(
            repo_name,
            title=f"{task.task_id} — {assignment.agent_id}",
            body="Automated PR.\n\nSyntax issues:\n"
            + ("\n".join(syntax_errors) if syntax_errors else "none"),
            head=assignment.branch_name,
            base="main",
        )
        pr_number = int(pr.get("number", 0)) or None
    except Exception as exc:
        logger.exception("create_pr: %s", exc)
        return AssignmentResult(
            agent_id=assignment.agent_id,
            task_id=task.task_id,
            branch_name=assignment.branch_name,
            success=False,
            error_message=str(exc),
            syntax_errors=syntax_errors,
        )
    return AssignmentResult(
        agent_id=assignment.agent_id,
        task_id=task.task_id,
        branch_name=assignment.branch_name,
        pr_number=pr_number,
        success=pr_number is not None,
        syntax_errors=syntax_errors,
        error_message="" if not syntax_errors else "non-fatal syntax warnings",
    )
