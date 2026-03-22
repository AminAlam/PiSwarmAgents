"""Syntax validation for generated Python files."""

from __future__ import annotations

import logging
import os
import py_compile
import tempfile

logger = logging.getLogger(__name__)


def check_python_syntax(files: dict[str, str]) -> list[str]:
    """Return list of error strings; empty means all valid."""
    errors: list[str] = []
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        tmp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(content)
                tmp = f.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{path}: {exc}")
        except Exception as exc:
            logger.exception("syntax check failed for %s: %s", path, exc)
            errors.append(f"{path}: {exc}")
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.unlink(tmp)
                except OSError as unlink_exc:
                    logger.warning("temp unlink failed: %s", unlink_exc)
    return errors
