"""FastAPI worker — receives assignments from orchestrator."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.config import GiteaConfig, LLMConfig, WorkerConfig, load_yaml_config
from src.git_ops import GiteaClient
from src.llm import SwarmLLM
from src.models import AgentRegistration, WorkerAssignmentRequest
from src.worker.coder import execute_assignment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


class WorkerState:
    """Per-process worker state."""

    def __init__(self) -> None:
        self.busy: bool = False
        self.current_task_id: str | None = None
        self.gitea: GiteaClient | None = None
        self.llm: SwarmLLM | None = None
        self.worker_cfg: WorkerConfig | None = None
        self.gitea_cfg: GiteaConfig | None = None
        self.llm_cfg: LLMConfig | None = None
        self.config_path: str = ""
        self.orchestrator_http: httpx.AsyncClient | None = None


state = WorkerState()


async def _register_loop() -> None:
    while True:
        try:
            await asyncio.sleep(300)
            if state.worker_cfg and state.orchestrator_http:
                reg = AgentRegistration(
                    agent_id=state.worker_cfg.agent_id,
                    host=state.worker_cfg.advertise_host,
                    port=state.worker_cfg.port,
                )
                url = f"{state.worker_cfg.orchestrator_url.rstrip('/')}/agents/register"
                await state.orchestrator_http.post(url, json=reg.model_dump())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("re-register failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    wc = WorkerConfig()
    gc = GiteaConfig()
    lc = LLMConfig()
    cfg_path = os.environ.get("SWARM_CONFIG_PATH", "config/swarm_config.yaml")
    _ = load_yaml_config(cfg_path)
    state.worker_cfg = wc
    state.gitea_cfg = gc
    state.llm_cfg = lc
    state.config_path = cfg_path
    state.orchestrator_http = httpx.AsyncClient(timeout=30.0)
    gitea_url = gc.url
    reg_url = f"{wc.orchestrator_url.rstrip('/')}/agents/register"
    reg = AgentRegistration(
        agent_id=wc.agent_id,
        host=wc.advertise_host,
        port=wc.port,
    )
    try:
        if state.orchestrator_http:
            r = await state.orchestrator_http.post(reg_url, json=reg.model_dump())
            if r.status_code == 200:
                data = r.json()
                gitea_url = str(data.get("gitea_url", gitea_url))
    except Exception as exc:
        logger.exception("Initial registration failed: %s", exc)
    state.gitea = GiteaClient(gitea_url, gc.token, gc.organization)
    state.llm = SwarmLLM(lc.hf_model, lc.n_ctx, lc.n_threads, lc.idle_timeout_seconds)
    bg = asyncio.create_task(_register_loop())
    yield
    bg.cancel()
    try:
        await bg
    except asyncio.CancelledError:
        pass
    if state.gitea:
        await state.gitea.close()
    if state.orchestrator_http:
        await state.orchestrator_http.aclose()


app = FastAPI(title="Pi Swarm Worker", lifespan=lifespan)

# Must hold strong references to background tasks or GC may destroy them mid-execution.
_background_tasks: set[asyncio.Task[None]] = set()


async def _run_assignment(req: WorkerAssignmentRequest) -> None:
    """Execute assignment in background; report result to orchestrator."""
    try:
        res = await execute_assignment(
            req.assignment,
            req.task,
            state.llm,
            state.gitea,
            req.repo_name,
            state.config_path,
            None,
        )
        report_url = f"{state.worker_cfg.orchestrator_url.rstrip('/')}/agents/{state.worker_cfg.agent_id}/result"
        if state.orchestrator_http:
            try:
                await state.orchestrator_http.post(report_url, json=res.model_dump(mode="json"))
            except Exception as exc:
                logger.exception("report result failed: %s", exc)
    except Exception as exc:
        logger.exception("assignment failed: %s", exc)
    finally:
        state.busy = False
        state.current_task_id = None


@app.post("/assignments", response_model=None)
async def post_assignment(req: WorkerAssignmentRequest) -> JSONResponse | dict[str, str]:
    if state.busy:
        return JSONResponse({"detail": "busy"}, status_code=503)
    if not state.llm or not state.gitea or not state.worker_cfg:
        return JSONResponse({"detail": "not initialized"}, status_code=500)
    state.busy = True
    state.current_task_id = req.task.task_id
    task = asyncio.create_task(_run_assignment(req))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "accepted"}


@app.get("/status")
async def get_status() -> dict[str, Any]:
    return {
        "busy": state.busy,
        "task_id": state.current_task_id,
        "agent_id": state.worker_cfg.agent_id if state.worker_cfg else "",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "busy" if state.busy else "ok",
        "agent_id": state.worker_cfg.agent_id if state.worker_cfg else "",
    }


@app.post("/cancel")
async def cancel() -> dict[str, str]:
    return {"status": "not_implemented"}
