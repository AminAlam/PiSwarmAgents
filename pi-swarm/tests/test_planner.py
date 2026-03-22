"""Planner validation tests."""

from __future__ import annotations

import json
from pathlib import Path

from src.models import AgentNode, AgentRole, DevAssignment, Task, TaskPlan
from src.orchestrator.planner import fallback_single_agent_plan, validate_plan


def test_lead_planner_prompt_formats_without_extra_keys() -> None:
    """Braces in instructions must be escaped ({{ }}) so str.format does not KeyError."""
    tpl = (Path(__file__).resolve().parent.parent / "config/prompts/lead_planner.txt").read_text(
        encoding="utf-8",
    )
    out = tpl.format(
        agent_list_json="[]",
        file_list="(none)",
        task_id="tid001",
        repo_name="myrepo",
        schema_json=json.dumps({"x": 1}),
    )
    assert "tid001" in out
    assert "{agent_id}" in out or "agent_id" in out


def test_validate_plan_ok() -> None:
    agents = [
        AgentNode(agent_id="dev-01", role=AgentRole.DEVELOPER, host="127.0.0.1"),
    ]
    plan = TaskPlan(
        task_id="t1",
        summary="s",
        repo_name="r",
        assignments=[
            DevAssignment(
                agent_id="dev-01",
                description="d",
                branch_name="t1/dev-01/x",
                files_to_create=["a.py"],
            ),
        ],
    )
    ok, _ = validate_plan(plan, agents)
    assert ok


def test_fallback_plan() -> None:
    task = Task(task_id="tid", title="x", description="build", repo_name="r")
    p = fallback_single_agent_plan(task, "repo", "dev-01")
    assert len(p.assignments) == 1
