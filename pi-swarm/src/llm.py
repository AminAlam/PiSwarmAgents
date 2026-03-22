"""
LLM wrapper with lazy loading, idle unloading, and structured JSON output.

The model is identified by HF_MODEL (e.g. Qwen/Qwen3-4B-GGUF). On first call,
resolves a local .gguf via huggingface_hub and loads with llama-cpp-python.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llama_cpp import Llama

logger = logging.getLogger(__name__)


def truncate_to_fit(text: str, max_chars: int = 12000) -> str:
    """Keep first and last portions; insert truncation marker."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[... truncated ...]\n\n" + text[-half:]


def resolve_model_path(hf_model: str) -> str:
    """Download GGUF from HF hub or use local path string."""
    if os.path.isfile(hf_model) and hf_model.endswith(".gguf"):
        return hf_model
    if os.path.isdir(hf_model):
        import glob

        matches = glob.glob(os.path.join(hf_model, "**", "*.gguf"), recursive=True)
        if matches:
            q4 = [m for m in matches if "Q4_K_M" in m]
            return q4[0] if q4 else matches[0]
        return hf_model
    try:
        from huggingface_hub import HfApi, hf_hub_download

        api = HfApi()
        files = api.list_repo_files(repo_id=hf_model)
        ggufs = [f for f in files if str(f).endswith(".gguf")]
        preferred = [f for f in ggufs if "Q4_K_M" in f]
        pick = (preferred[0] if preferred else (ggufs[0] if ggufs else ""))
        if not pick:
            logger.warning("No GGUF in repo %s; using raw HF id", hf_model)
            return hf_model
        return str(hf_hub_download(repo_id=hf_model, filename=pick))
    except Exception as exc:
        logger.warning("HF resolve failed for %s: %s — using as path", hf_model, exc)
        return hf_model


class SwarmLLM:
    """Lazy llama.cpp wrapper with idle unload."""

    def __init__(
        self,
        hf_model: str,
        n_ctx: int = 4096,
        n_threads: int = 4,
        idle_timeout: int = 300,
    ) -> None:
        self._hf_model = hf_model
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._idle_timeout = idle_timeout
        self._model: Llama | None = None
        self._last_used: float = 0.0
        self._model_path: str | None = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                self._last_used = time.monotonic()
                return
            try:
                from llama_cpp import Llama

                path = resolve_model_path(self._hf_model)
                self._model_path = path
                logger.info("Loading LLM from %s", path)
                self._model = Llama(
                    model_path=path,
                    n_ctx=self._n_ctx,
                    n_threads=self._n_threads,
                    verbose=False,
                )
                self._last_used = time.monotonic()
            except Exception as exc:
                logger.exception("Failed to load model: %s", exc)
                self._model = None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> tuple[str, int, int]:
        """Return (text, prompt_tokens, completion_tokens)."""
        self._ensure_loaded()
        if self._model is None:
            return "", 0, 0
        self._last_used = time.monotonic()
        try:
            out = self._model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = ""
            if out and "choices" in out:
                choice = out["choices"][0]
                msg = choice.get("message") or {}
                text = str(msg.get("content", ""))
            usage = out.get("usage") or {}
            pin = int(usage.get("prompt_tokens", 0) or 0)
            cout = int(usage.get("completion_tokens", 0) or 0)
            return text, pin, cout
        except Exception as exc:
            logger.exception("LLM generate failed: %s", exc)
            return "", 0, 0

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema_description: str,
        max_tokens: int = 2048,
        retries: int = 3,
    ) -> tuple[dict[str, object], int, int]:
        """Generate JSON; retry on parse failure."""
        total_in = 0
        total_out = 0
        extra = f"\n\nRespond with ONLY valid JSON: {schema_description}"
        for attempt in range(retries):
            up = user_prompt + (extra if attempt else "")
            text, pin, cout = self.generate(
                system_prompt,
                up,
                max_tokens=max_tokens,
            )
            total_in += pin
            total_out += cout
            try:
                cleaned = text.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
                    cleaned = re.sub(r"\n```$", "", cleaned)
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    return data, total_in, total_out
            except json.JSONDecodeError as exc:
                logger.warning("JSON parse attempt %s failed: %s", attempt + 1, exc)
        return {}, total_in, total_out

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                logger.info("Unloading LLM")
                del self._model
                self._model = None

    def maybe_unload_if_idle(self) -> None:
        if self._model is None:
            return
        if time.monotonic() - self._last_used > self._idle_timeout:
            self.unload()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
