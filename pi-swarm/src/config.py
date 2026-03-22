"""
Loads configuration from environment variables and swarm_config.yaml.

Environment variables (set by Ansible systemd units) take precedence where
applicable. The YAML file provides defaults and prompt paths.

This is the ONLY place that reads os.environ or opens config files.
Every other module receives its config as constructor arguments.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class GiteaConfig(BaseSettings):
    """Gitea API settings from environment."""

    model_config = SettingsConfigDict(
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
    )

    url: str = Field(validation_alias="GITEA_API_BASE_URL", default="http://127.0.0.1:3000")
    token: str = Field(validation_alias="GITEA_TOKEN", default="")
    organization: str = "swarm"


class LLMConfig(BaseSettings):
    """Local LLM settings."""

    model_config = SettingsConfigDict(
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
    )

    hf_model: str = Field(validation_alias="HF_MODEL", default="Qwen/Qwen3-4B-GGUF")
    n_ctx: int = 4096
    n_threads: int = 4
    temperature: float = 0.2
    max_tokens: int = 2048
    idle_timeout_seconds: int = 300


class OrchestratorConfig(BaseSettings):
    """Orchestrator (lead) HTTP server and behavior."""

    model_config = SettingsConfigDict(
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080
    metrics_db: str = Field(
        validation_alias="SWARM_METRICS_DB",
        default="/var/lib/pi-swarm/metrics.db",
    )
    config_path: str = Field(
        validation_alias="SWARM_CONFIG_PATH",
        default="/opt/pi-swarm/app/config/swarm_config.yaml",
    )
    max_review_rounds: int = 3
    auto_merge_on_approval: bool = True


class WorkerConfig(BaseSettings):
    """Worker (dev node) settings."""

    model_config = SettingsConfigDict(
        env_prefix="",
        populate_by_name=True,
        extra="ignore",
    )

    orchestrator_url: str = Field(validation_alias="ORCHESTRATOR_URL", default="")
    agent_id: str = Field(validation_alias="AGENT_ID", default="dev-01")
    advertise_host: str = Field(validation_alias="WORKER_ADVERTISE_HOST", default="127.0.0.1")
    port: int = 8000


class SwarmYamlConfig(BaseModel):
    """Values loaded only from YAML (no env)."""

    webhook_base_url: str = "http://127.0.0.1:8080"
    prompts: dict[str, str] = Field(default_factory=dict)


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load YAML file; return empty dict if missing or on error."""
    p = Path(path)
    if not p.is_file():
        logger.warning("Config file not found: %s", p)
        return {}
    try:
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.exception("Failed to load YAML config %s: %s", p, exc)
        return {}


def merge_swarm_yaml(raw: dict[str, Any]) -> SwarmYamlConfig:
    """Build SwarmYamlConfig from parsed YAML."""
    prompts = raw.get("prompts")
    if not isinstance(prompts, dict):
        prompts = {}
    wb = raw.get("webhook_base_url", "http://127.0.0.1:8080")
    return SwarmYamlConfig(webhook_base_url=str(wb), prompts={str(k): str(v) for k, v in prompts.items()})


def resolve_prompt_path(config_dir: Path, rel_or_abs: str) -> Path:
    """Resolve prompt file path relative to cwd or absolute."""
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (config_dir / p).resolve()


def read_prompt_file(config_path: str, prompt_key: str, cwd: Path | None = None) -> str:
    """Read a prompt template from YAML-configured path."""
    cwd = cwd or Path(os.getcwd())
    raw = load_yaml_config(config_path)
    merged = merge_swarm_yaml(raw)
    rel = merged.prompts.get(prompt_key, "")
    if not rel:
        logger.warning("Missing prompt key %s in config", prompt_key)
        return ""
    cfg_dir = Path(config_path).parent
    path = resolve_prompt_path(cfg_dir, rel)
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("Failed to read prompt %s: %s", path, exc)
        return ""
