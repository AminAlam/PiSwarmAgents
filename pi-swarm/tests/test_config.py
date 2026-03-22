"""Config path helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.config import _prompt_candidate_paths, read_prompt_file


def test_read_prompt_file_resolves_beside_swarm_config() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / "config" / "swarm_config.yaml"
        cfg.parent.mkdir(parents=True)
        (root / "config" / "prompts").mkdir(parents=True)
        (root / "config" / "prompts" / "lead_planner.txt").write_text("HELLO", encoding="utf-8")
        cfg.write_text(
            "prompts:\n  lead_planner: prompts/lead_planner.txt\n",
            encoding="utf-8",
        )
        assert read_prompt_file(str(cfg), "lead_planner") == "HELLO"


def test_prompt_candidates_include_package_and_cwd() -> None:
    c = _prompt_candidate_paths("/tmp/wrong/config/swarm_config.yaml", "prompts/lead_planner.txt")
    assert len(c) >= 2
    assert "wrong" in str(c[0]) and "prompts" in str(c[0])
    assert any("pi-swarm" in str(p) and "config" in str(p) for p in c[1:])


def test_read_prompt_file_strips_config_prefix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = root / "config" / "swarm_config.yaml"
        cfg.parent.mkdir(parents=True)
        (root / "config" / "prompts").mkdir(parents=True)
        (root / "config" / "prompts" / "x.txt").write_text("OK", encoding="utf-8")
        cfg.write_text(
            "prompts:\n  lead_planner: config/prompts/x.txt\n",
            encoding="utf-8",
        )
        assert read_prompt_file(str(cfg), "lead_planner") == "OK"
