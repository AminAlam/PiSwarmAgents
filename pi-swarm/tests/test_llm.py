"""LLM helper tests (no model load)."""

from __future__ import annotations

from src.llm import truncate_to_fit


def test_truncate_to_fit() -> None:
    s = "x" * 20000
    out = truncate_to_fit(s, max_chars=1000)
    assert "[... truncated ...]" in out
    assert len(out) < len(s)
