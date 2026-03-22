"""Gitea client structure tests."""

from __future__ import annotations

from src.git_ops import GiteaClient


def test_client_props() -> None:
    c = GiteaClient("http://127.0.0.1:3000", "tok", "swarm")
    assert c.base_url == "http://127.0.0.1:3000"
    assert c.organization == "swarm"
