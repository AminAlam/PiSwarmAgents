"""Gitea repo/file operations (httpx async)."""

from __future__ import annotations

import base64
import logging
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GiteaClientCore:
    """Repo and file API."""

    def __init__(self, base_url: str, token: str, org: str = "swarm") -> None:
        self._base = base_url.rstrip("/")
        self._org = org
        self._headers = {
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers,
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_connections=20),
        )

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def organization(self) -> str:
        return self._org

    def _repo_path(self, repo: str) -> str:
        return f"/api/v1/repos/{self._org}/{repo}"

    async def create_repo(
        self,
        name: str,
        description: str = "",
        auto_init: bool = True,
    ) -> dict[str, Any]:
        url = f"/api/v1/orgs/{self._org}/repos"
        body = {
            "name": name,
            "description": description,
            "auto_init": auto_init,
            "default_branch": "main",
        }
        try:
            r = await self._client.post(url, json=body)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("create_repo failed: %s", exc)
            raise

    async def repo_exists(self, name: str) -> bool:
        try:
            r = await self._client.get(self._repo_path(name))
            return r.status_code == 200
        except Exception as exc:
            logger.warning("repo_exists error: %s", exc)
            return False

    async def list_files(
        self,
        repo: str,
        branch: str = "main",
        path: str = "",
    ) -> list[str]:
        out: list[str] = []
        queue: deque[str] = deque([path])
        try:
            while queue:
                cur = queue.popleft()
                seg = cur.strip("/")
                base = f"{self._repo_path(repo)}/contents"
                url = f"{base}/{seg}" if seg else base
                r = await self._client.get(url, params={"ref": branch})
                if r.status_code != 200:
                    continue
                data = r.json()
                if not isinstance(data, list):
                    continue
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("path", ""))
                    typ = item.get("type", "")
                    if typ == "file":
                        out.append(name)
                    elif typ == "dir" and name:
                        queue.append(name)
            return sorted(out)
        except Exception as exc:
            logger.exception("list_files failed: %s", exc)
            return []

    async def get_file_content(self, repo: str, path: str, branch: str = "main") -> str:
        p = path.strip("/")
        url = f"{self._repo_path(repo)}/raw/{p}"
        try:
            r = await self._client.get(url, params={"ref": branch})
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.exception("get_file_content failed: %s", exc)
            return ""

    async def create_branch(
        self,
        repo: str,
        branch_name: str,
        from_branch: str = "main",
    ) -> dict[str, Any]:
        url = f"{self._repo_path(repo)}/branches"
        body = {"new_branch_name": branch_name, "old_branch_name": from_branch}
        try:
            r = await self._client.post(url, json=body)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("create_branch failed: %s", exc)
            raise

    async def _get_file_sha(self, repo: str, filepath: str, branch: str) -> str | None:
        fp = filepath.strip("/")
        url = f"{self._repo_path(repo)}/contents/{fp}"
        try:
            r = await self._client.get(url, params={"ref": branch})
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, dict):
                return str(data.get("sha", "")) or None
        except Exception as exc:
            logger.warning("get sha failed: %s", exc)
        return None

    async def create_or_update_file(
        self,
        repo: str,
        branch: str,
        filepath: str,
        content: str,
        message: str,
    ) -> dict[str, Any]:
        fp = filepath.strip("/")
        url = f"{self._repo_path(repo)}/contents/{fp}"
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body: dict[str, Any] = {
            "branch": branch,
            "content": b64,
            "message": message,
        }
        sha = await self._get_file_sha(repo, fp, branch)
        if sha:
            body["sha"] = sha
        try:
            r = await self._client.put(url, json=body)
            r.raise_for_status()
            return dict(r.json())
        except Exception as exc:
            logger.exception("create_or_update_file failed: %s", exc)
            raise

    async def push_files(
        self,
        repo: str,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for fp, content in files.items():
            msg = f"{message} ({fp})" if len(files) > 1 else message
            try:
                res = await self.create_or_update_file(repo, branch, fp, content, msg)
                results.append(res)
            except Exception as exc:
                logger.error("push_files failed for %s: %s", fp, exc)
                results.append({"error": str(exc), "path": fp})
        return results

    async def close(self) -> None:
        await self._client.aclose()
