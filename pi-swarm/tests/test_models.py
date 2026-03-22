"""Serialization tests for Pydantic models."""

from __future__ import annotations

import json

from src.models import DevAssignment, Task, TaskPlan, TaskStatus


def test_task_roundtrip() -> None:
    t = Task(
        task_id="abc123",
        title="t",
        description="d",
        repo_name="r",
        status=TaskStatus.PENDING,
    )
    raw = t.model_dump(mode="json")
    t2 = Task.model_validate(raw)
    assert t2.task_id == "abc123"
    assert t2.status == TaskStatus.PENDING


def test_task_plan_json() -> None:
    p = TaskPlan(
        task_id="x",
        summary="s",
        repo_name="repo",
        assignments=[
            DevAssignment(
                agent_id="dev-01",
                description="do",
                branch_name="b",
                files_to_create=["a.py"],
            ),
        ],
    )
    s = p.model_dump_json()
    p2 = TaskPlan.model_validate_json(s)
    assert len(p2.assignments) == 1
    assert json.loads(s)["repo_name"] == "repo"
