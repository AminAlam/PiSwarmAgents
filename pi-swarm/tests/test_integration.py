"""Integration tests (optional real services)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PI_SWARM_INTEGRATION"),
    reason="Set PI_SWARM_INTEGRATION=1 and Gitea URL to run",
)


@pytest.mark.asyncio
async def test_gitea_ping() -> None:
    from src.git_ops import GiteaClient

    base = os.environ.get("GITEA_API_BASE_URL", "")
    tok = os.environ.get("GITEA_TOKEN", "")
    if not base or not tok:
        pytest.skip("Gitea env not set")
    c = GiteaClient(base, tok)
    try:
        assert await c.repo_exists("nonexistent-swarm-test-xyz") is False
    finally:
        await c.close()
