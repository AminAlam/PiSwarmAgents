"""Pydantic models — single source of truth for cross-component messages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    REVISION = "revision"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentRole(str, Enum):
    LEAD = "lead"
    DEVELOPER = "developer"


class DevAssignment(BaseModel):
    """A single unit of work for one dev agent."""

    agent_id: str
    description: str
    files_to_create: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    branch_name: str
    depends_on: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class TaskPlan(BaseModel):
    """Lead's decomposition of a task into assignments."""

    task_id: str
    summary: str
    assignments: list[DevAssignment]
    repo_name: str
    shared_interfaces: str = ""
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class Task(BaseModel):
    """Top-level task submitted to the swarm."""

    task_id: str
    title: str
    description: str
    repo_name: str = ""
    repo_url: str = ""
    language: str = "python"
    constraints: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    plan: TaskPlan | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class PRReview(BaseModel):
    """Lead's review of a pull request."""

    pr_number: int
    agent_id: str
    task_id: str
    approved: bool
    comments: list[str] = Field(default_factory=list)
    conflicts_with: list[int] = Field(default_factory=list)
    suggested_changes: str = ""


class AgentNode(BaseModel):
    """A registered node in the swarm."""

    agent_id: str
    role: AgentRole
    host: str
    port: int = 8000
    status: str = "idle"
    current_task_id: str | None = None
    capabilities: list[str] = Field(default_factory=lambda: ["python"])


class AgentRegistration(BaseModel):
    """Sent by workers at startup: POST /agents/register."""

    agent_id: str
    host: str
    port: int = 8000
    capabilities: list[str] = Field(default_factory=lambda: ["python"])


class AgentRegistrationResponse(BaseModel):
    """Returned by orchestrator on registration."""

    ok: bool
    gitea_url: str
    gitea_org: str = "swarm"


class AssignmentResult(BaseModel):
    """Worker reports back after completing (or failing) an assignment."""

    agent_id: str
    task_id: str
    branch_name: str
    pr_number: int | None = None
    success: bool
    error_message: str = ""
    syntax_errors: list[str] = Field(default_factory=list)


class WorkerAssignmentRequest(BaseModel):
    """Orchestrator → worker payload for /assignments."""

    task: Task
    assignment: DevAssignment
    repo_name: str


class MetricsRecord(BaseModel):
    """Single metrics data point."""

    task_id: str
    agent_id: str
    event: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_seconds: float = 0.0
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TaskSubmitRequest(BaseModel):
    """POST /tasks body."""

    title: str
    description: str
    language: str = "python"
    repo_name: str
    constraints: list[str] = Field(default_factory=list)


class GiteaWebhookPayload(BaseModel):
    """Minimal fields parsed from Gitea webhook JSON."""

    action: str | None = None
    pull_request: dict[str, object] | None = None
    repository: dict[str, object] | None = None
