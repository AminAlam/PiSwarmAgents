"""Aggregate queries for dashboard (keeps collector.py smaller)."""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


async def task_summary(db_path: str, task_id: str) -> dict[str, Any]:
    try:
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute(
                """
                SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),
                       COALESCE(SUM(duration_seconds),0), COUNT(*)
                FROM metrics WHERE task_id = ?
                """,
                (task_id,),
            )
            row = await cur.fetchone()
            tin, tout, dur, cnt = row if row else (0, 0, 0, 0)
            return {
                "task_id": task_id,
                "tokens_in": int(tin),
                "tokens_out": int(tout),
                "duration_seconds": float(dur),
                "event_count": int(cnt),
            }
    except Exception as exc:
        logger.exception("task_summary failed: %s", exc)
        return {"task_id": task_id, "error": str(exc)}


async def recent_metrics(db_path: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT t.task_id, t.title, t.status,
                       (SELECT COALESCE(SUM(m.tokens_in + m.tokens_out), 0)
                        FROM metrics m WHERE m.task_id = t.task_id) AS tokens
                FROM tasks t
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("recent_metrics failed: %s", exc)
        return []
