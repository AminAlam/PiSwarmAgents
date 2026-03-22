"""Async SQLite metrics using aiosqlite."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from src.metrics.aggregates import recent_metrics as agg_recent_metrics
from src.metrics.aggregates import task_summary as agg_task_summary
from src.models import AgentNode, AgentRole, MetricsRecord, Task, TaskStatus

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetricsCollector:
    """SQLite-backed task, agent, and event metrics."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tokens_in INTEGER DEFAULT 0,
                    tokens_out INTEGER DEFAULT 0,
                    duration_seconds REAL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT
                )
                """,
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    language TEXT,
                    status TEXT,
                    plan_json TEXT,
                    repo_url TEXT,
                    repo_name TEXT,
                    created_at TEXT,
                    completed_at TEXT
                )
                """,
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    role TEXT,
                    host TEXT,
                    port INTEGER,
                    status TEXT,
                    last_seen TEXT,
                    current_task_id TEXT,
                    capabilities_json TEXT
                )
                """,
            )
            await db.commit()
            try:
                await db.execute("ALTER TABLE tasks ADD COLUMN repo_name TEXT")
                await db.commit()
            except Exception:
                pass
        logger.info("Metrics DB initialized at %s", self._db_path)

    async def log_event(self, record: MetricsRecord) -> None:
        meta = json.dumps(record.metadata)
        ts = record.timestamp.isoformat()
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT INTO metrics (
                        task_id, agent_id, event, tokens_in, tokens_out,
                        duration_seconds, timestamp, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.task_id,
                        record.agent_id,
                        record.event,
                        record.tokens_in,
                        record.tokens_out,
                        record.duration_seconds,
                        ts,
                        meta,
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("log_event failed: %s", exc)

    async def save_task(self, task: Task) -> None:
        plan_json = task.plan.model_dump_json() if task.plan else ""
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO tasks (
                        task_id, title, description, language, status,
                        plan_json, repo_url, repo_name, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.title,
                        task.description,
                        task.language,
                        task.status.value,
                        plan_json,
                        task.repo_url,
                        task.repo_name,
                        task.created_at.isoformat(),
                        None,
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("save_task failed: %s", exc)

    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                completed = _utc_now() if status == TaskStatus.COMPLETED else None
                await db.execute(
                    "UPDATE tasks SET status = ?, completed_at = COALESCE(?, completed_at) WHERE task_id = ?",
                    (status.value, completed, task_id),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("update_task_status failed: %s", exc)

    async def get_task(self, task_id: str) -> Task | None:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM tasks WHERE task_id = ?",
                    (task_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return self._row_to_task(dict(row))
        except Exception as exc:
            logger.exception("get_task failed: %s", exc)
            return None

    def _row_to_task(self, row: dict[str, Any]) -> Task:
        from src.models import TaskPlan

        plan = None
        if row.get("plan_json"):
            try:
                plan = TaskPlan.model_validate_json(str(row["plan_json"]))
            except Exception:
                plan = None
        ca = row.get("created_at")
        created = datetime.now(timezone.utc)
        if isinstance(ca, str) and ca:
            try:
                created = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            except ValueError:
                pass
        return Task(
            task_id=str(row["task_id"]),
            title=str(row["title"] or ""),
            description=str(row["description"] or ""),
            repo_url=str(row["repo_url"] or ""),
            repo_name=str(row.get("repo_name") or ""),
            language=str(row["language"] or "python"),
            status=TaskStatus(str(row["status"] or "pending")),
            plan=plan,
            created_at=created,
        )

    async def list_tasks(self, limit: int = 50) -> list[Task]:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                rows = await cur.fetchall()
                return [self._row_to_task(dict(r)) for r in rows]
        except Exception as exc:
            logger.exception("list_tasks failed: %s", exc)
            return []

    async def register_agent(self, node: AgentNode) -> None:
        caps = json.dumps(node.capabilities)
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO agents (
                        agent_id, role, host, port, status, last_seen,
                        current_task_id, capabilities_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.agent_id,
                        node.role.value,
                        node.host,
                        node.port,
                        node.status,
                        _utc_now(),
                        node.current_task_id,
                        caps,
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("register_agent failed: %s", exc)

    async def update_agent_status(self, agent_id: str, status: str) -> None:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE agents SET status = ?, last_seen = ? WHERE agent_id = ?",
                    (status, _utc_now(), agent_id),
                )
                await db.commit()
        except Exception as exc:
            logger.exception("update_agent_status failed: %s", exc)

    async def get_agents(self) -> list[AgentNode]:
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM agents")
                rows = await cur.fetchall()
                out: list[AgentNode] = []
                for r in rows:
                    row = dict(r)
                    caps_raw = row.get("capabilities_json") or "[]"
                    try:
                        caps = list(json.loads(str(caps_raw)))
                    except json.JSONDecodeError:
                        caps = ["python"]
                    out.append(
                        AgentNode(
                            agent_id=str(row["agent_id"]),
                            role=AgentRole(str(row["role"] or "developer")),
                            host=str(row["host"] or ""),
                            port=int(row["port"] or 8000),
                            status=str(row["status"] or "idle"),
                            current_task_id=row.get("current_task_id"),
                            capabilities=[str(c) for c in caps],
                        ),
                    )
                return out
        except Exception as exc:
            logger.exception("get_agents failed: %s", exc)
            return []

    async def task_summary(self, task_id: str) -> dict[str, Any]:
        return await agg_task_summary(self._db_path, task_id)

    async def recent_metrics(self, limit: int = 10) -> list[dict[str, Any]]:
        return await agg_recent_metrics(self._db_path, limit)
