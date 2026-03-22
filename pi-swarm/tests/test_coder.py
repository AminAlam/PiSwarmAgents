"""Coder parsing and executor tests."""

from __future__ import annotations

from src.worker.coder import parse_file_blocks
from src.worker.executor import check_python_syntax


def test_parse_file_blocks() -> None:
    out = """FILE: foo.py
```python
x = 1
```
"""
    d = parse_file_blocks(out)
    assert "foo.py" in d
    assert "x = 1" in d["foo.py"]


def test_syntax_ok() -> None:
    errs = check_python_syntax({"a.py": "def f():\n    return 1\n"})
    assert errs == []


def test_syntax_bad() -> None:
    errs = check_python_syntax({"a.py": "def f(\n"})
    assert errs
