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
import shutil
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
    logger.info("[resolve_model_path] Resolving model: %s", hf_model)

    if os.path.isfile(hf_model) and hf_model.endswith(".gguf"):
        size_mb = os.path.getsize(hf_model) / (1024 * 1024)
        logger.info("[resolve_model_path] Found local GGUF file: %s (%.0f MB)", hf_model, size_mb)
        return hf_model

    if os.path.isdir(hf_model):
        import glob

        matches = glob.glob(os.path.join(hf_model, "**", "*.gguf"), recursive=True)
        if matches:
            q4 = [m for m in matches if "Q4_K_M" in m]
            pick = q4[0] if q4 else matches[0]
            size_mb = os.path.getsize(pick) / (1024 * 1024)
            logger.info("[resolve_model_path] Found local GGUF in dir: %s (%.0f MB)", pick, size_mb)
            return pick
        logger.warning("[resolve_model_path] Directory %s has no .gguf files", hf_model)
        return hf_model

    try:
        from huggingface_hub import HfApi, hf_hub_download

        logger.info("[resolve_model_path] Listing files in HF repo: %s", hf_model)
        api = HfApi()
        files = api.list_repo_files(repo_id=hf_model)
        ggufs = [f for f in files if str(f).endswith(".gguf")]
        logger.info("[resolve_model_path] Found %d GGUF files: %s", len(ggufs), ggufs)
        preferred = [f for f in ggufs if "Q4_K_M" in f]
        pick = preferred[0] if preferred else (ggufs[0] if ggufs else "")
        if not pick:
            logger.warning("[resolve_model_path] No GGUF in repo %s; using raw HF id", hf_model)
            return hf_model
        logger.info("[resolve_model_path] Downloading %s/%s (this may take a long time on Pi)...", hf_model, pick)
        t0 = time.perf_counter()
        local_path = str(hf_hub_download(repo_id=hf_model, filename=pick))
        dur = time.perf_counter() - t0
        size_mb = os.path.getsize(local_path) / (1024 * 1024)
        logger.info(
            "[resolve_model_path] Download complete: %s (%.0f MB) in %.1f s",
            local_path, size_mb, dur,
        )
        return local_path
    except Exception as exc:
        logger.exception("[resolve_model_path] HF resolve failed for %s: %s", hf_model, exc)
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
            logger.info("[LLM] _ensure_loaded: model not loaded yet, starting load sequence")
            try:
                # Log system resources before loading
                try:
                    import resource

                    mem = resource.getrusage(resource.RUSAGE_SELF)
                    logger.info(
                        "[LLM] Memory before load: maxrss=%.0f MB",
                        mem.ru_maxrss / 1024,  # macOS gives bytes, Linux gives KB
                    )
                except Exception:
                    pass
                disk = shutil.disk_usage("/")
                logger.info(
                    "[LLM] Disk space: total=%.1f GB, free=%.1f GB",
                    disk.total / (1024**3),
                    disk.free / (1024**3),
                )

                logger.info("[LLM] Importing llama_cpp...")
                t0 = time.perf_counter()
                from llama_cpp import Llama

                logger.info("[LLM] llama_cpp imported in %.1f s", time.perf_counter() - t0)

                logger.info("[LLM] Resolving model path for: %s", self._hf_model)
                t0 = time.perf_counter()
                path = resolve_model_path(self._hf_model)
                self._model_path = path
                logger.info("[LLM] Model path resolved in %.1f s: %s", time.perf_counter() - t0, path)

                if not os.path.isfile(path):
                    logger.error("[LLM] Model file does NOT exist at resolved path: %s", path)
                    self._model = None
                    return

                size_mb = os.path.getsize(path) / (1024 * 1024)
                logger.info(
                    "[LLM] Loading model into RAM: %s (%.0f MB), n_ctx=%d, n_threads=%d",
                    path, size_mb, self._n_ctx, self._n_threads,
                )
                t0 = time.perf_counter()
                self._model = Llama(
                    model_path=path,
                    n_ctx=self._n_ctx,
                    n_threads=self._n_threads,
                    verbose=True,
                )
                dur = time.perf_counter() - t0
                logger.info("[LLM] Model loaded successfully in %.1f s", dur)
                self._last_used = time.monotonic()
            except Exception as exc:
                logger.exception("[LLM] Failed to load model: %s", exc)
                self._model = None

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> tuple[str, int, int]:
        """Return (text, prompt_tokens, completion_tokens)."""
        logger.info("[LLM] generate() called, max_tokens=%d", max_tokens)
        self._ensure_loaded()
        if self._model is None:
            logger.error("[LLM] generate() aborted: model is None (failed to load)")
            return "", 0, 0
        self._last_used = time.monotonic()
        prompt_len = len(system_prompt) + len(user_prompt)
        logger.info("[LLM] Starting inference, prompt ~%d chars...", prompt_len)
        t0 = time.perf_counter()
        try:
            out = self._model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            dur = time.perf_counter() - t0
            text = ""
            if out and "choices" in out:
                choice = out["choices"][0]
                msg = choice.get("message") or {}
                text = str(msg.get("content", ""))
            usage = out.get("usage") or {}
            pin = int(usage.get("prompt_tokens", 0) or 0)
            cout = int(usage.get("completion_tokens", 0) or 0)
            logger.info(
                "[LLM] Inference done in %.1f s: prompt_tokens=%d, completion_tokens=%d, output_len=%d chars",
                dur, pin, cout, len(text),
            )
            if not text:
                logger.warning("[LLM] Model returned empty text")
            return text, pin, cout
        except Exception as exc:
            dur = time.perf_counter() - t0
            logger.exception("[LLM] generate failed after %.1f s: %s", dur, exc)
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
        logger.info("[LLM] generate_json() called, retries=%d", retries)
        total_in = 0
        total_out = 0
        extra = f"\n\nRespond with ONLY valid JSON matching this schema: {schema_description}"
        for attempt in range(retries):
            logger.info("[LLM] generate_json attempt %d/%d", attempt + 1, retries)
            up = user_prompt + extra
            text, pin, cout = self.generate(
                system_prompt,
                up,
                max_tokens=max_tokens,
            )
            total_in += pin
            total_out += cout
            if not text:
                logger.warning("[LLM] generate_json attempt %d: empty response from model", attempt + 1)
                continue
            try:
                cleaned = text.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
                    cleaned = re.sub(r"\n```$", "", cleaned)
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    logger.info("[LLM] generate_json: valid JSON parsed on attempt %d", attempt + 1)
                    return data, total_in, total_out
                logger.warning("[LLM] generate_json attempt %d: parsed JSON is not a dict: %s", attempt + 1, type(data))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "[LLM] generate_json attempt %d: JSON parse failed: %s — raw text (first 500 chars): %s",
                    attempt + 1, exc, text[:500],
                )
        logger.error("[LLM] generate_json: all %d attempts failed, returning empty dict", retries)
        return {}, total_in, total_out

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                logger.info("[LLM] Unloading model")
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
