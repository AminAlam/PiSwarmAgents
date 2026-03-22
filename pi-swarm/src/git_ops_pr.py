"""Pull request and webhook helpers for GiteaClient (extends core)."""

from __future__ import annotations

import logging
from typing import Any

from src.git_ops_core import GiteaClientCore

logger = logging.getLogger(__name__)


class GiteaClient(GiteaClientCore):
    """Full client: core repo ops + PRs and hooks."""

    async def create_pr(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/pulls"
        payload = {"title": title, "body": body, "head": head, "base": base}
        try:
            r = await self._client.post(url, json=payload)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("create_pr failed: %s", exc)
            raise

    async def get_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/pulls/{pr_number}"
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("get_pr failed: %s", exc)
            return {}

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        url = f"{self._repo_path(repo)}/pulls/{pr_number}.diff"
        try:
            r = await self._client.get(url, headers={"Accept": "text/plain"})
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.exception("get_pr_diff failed: %s", exc)
            return ""

    async def list_open_prs(self, repo: str) -> list[dict[str, Any]]:
        url = f"{self._repo_path(repo)}/pulls"
        try:
            r = await self._client.get(url, params={"state": "open"})
            r.raise_for_status()
            data = r.json()
            return list(data) if isinstance(data, list) else []
        except Exception as exc:
            logger.exception("list_open_prs failed: %s", exc)
            return []

    async def merge_pr(
        self,
        repo: str,
        pr_number: int,
        merge_type: str = "merge",
    ) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/pulls/{pr_number}/merge"
        try:
            r = await self._client.post(url, json={"Do": merge_type})
            if r.status_code >= 400:
                return {"error": r.text, "status_code": r.status_code}
            return dict(r.json()) if r.content else {}
        except Exception as exc:
            logger.exception("merge_pr failed: %s", exc)
            return {"error": str(exc)}

    async def add_pr_comment(self, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/issues/{pr_number}/comments"
        try:
            r = await self._client.post(url, json={"body": body})
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("add_pr_comment failed: %s", exc)
            return {}

    async def close_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/pulls/{pr_number}"
        try:
            r = await self._client.patch(url, json={"state": "closed"})
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("close_pr failed: %s", exc)
            return {}

    async def setup_webhook(
        self,
        repo: str,
        target_url: str,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        ev = events or ["pull_request"]
        url = f"{self._repo_path(repo)}/hooks"
        body = {
            "type": "gitea",
            "config": {"url": target_url, "content_type": "json"},
            "events": ev,
            "active": True,
        }
        try:
            r = await self._client.post(url, json=body)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("setup_webhook failed: %s", exc)
            return {}
