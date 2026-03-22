"""PR review via lead LLM."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from src.git_ops import GiteaClient
from src.llm import SwarmLLM, truncate_to_fit
from src.models import DevAssignment, PRReview

if TYPE_CHECKING:
    from src.metrics.collector import MetricsCollector

logger = logging.getLogger(__name__)


async def review_pr(
    gitea: GiteaClient,
    repo: str,
    pr_number: int,
    assignment: DevAssignment,
    task_id: str,
    llm: SwarmLLM,
    reviewer_prompt_template: str,
    metrics: MetricsCollector | None,
) -> PRReview:
    """Produce PRReview from diff and assignment context."""
    diff = ""
    other_summary = "[]"
    try:
        diff = await gitea.get_pr_diff(repo, pr_number)
        others = await gitea.list_open_prs(repo)
        brief = [{"number": o.get("number"), "title": o.get("title")} for o in others if o.get("number") != pr_number]
        other_summary = json.dumps(brief[:20])
    except Exception as exc:
        logger.exception("review_pr fetch failed: %s", exc)

    approx_tokens = len(diff) // 4
    if approx_tokens > 2500:
        diff = truncate_to_fit(diff, 10000)
    crit = "\n".join(f"- {c}" for c in assignment.acceptance_criteria)
    schema = {
        "pr_number": pr_number,
        "agent_id": assignment.agent_id,
        "task_id": task_id,
        "approved": True,
        "comments": [],
        "conflicts_with": [],
        "suggested_changes": "",
    }
    schema_json = json.dumps(schema)
    user = reviewer_prompt_template.format(
        assignment_description=assignment.description,
        acceptance_criteria_list=crit or "(none)",
        diff=diff,
        other_prs_summary=other_summary,
        pr_number=pr_number,
        agent_id=assignment.agent_id,
        task_id=task_id,
        schema_json=schema_json,
    )
    system = "You are a senior code reviewer. Output only JSON."
    t0 = time.perf_counter()
    data, tin, tout = await __import__("asyncio").to_thread(
        llm.generate_json,
        system,
        user,
        schema_json,
    )
    dur = time.perf_counter() - t0
    if metrics:
        from src.models import MetricsRecord

        await metrics.log_event(
            MetricsRecord(
                task_id=task_id,
                agent_id="lead",
                event="reviewer_llm",
                tokens_in=tin,
                tokens_out=tout,
                duration_seconds=dur,
                metadata={"pr": str(pr_number)},
            ),
        )
    try:
        if data:
            data["pr_number"] = pr_number
            data["agent_id"] = assignment.agent_id
            data["task_id"] = task_id
            return PRReview.model_validate(data)
    except Exception as exc:
        logger.exception("PRReview parse failed: %s", exc)
    return PRReview(
        pr_number=pr_number,
        agent_id=assignment.agent_id,
        task_id=task_id,
        approved=False,
        comments=["Automatic fallback: review parse failed"],
    )
