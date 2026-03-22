"""FastAPI orchestrator (lead node)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from src.config import (
    GiteaConfig,
    LLMConfig,
    OrchestratorConfig,
    load_yaml_config,
    merge_swarm_yaml,
)
from src.git_ops import GiteaClient
from src.llm import SwarmLLM
from src.metrics.collector import MetricsCollector
from src.metrics.dashboard import render_dashboard
from src.models import (
    AgentNode,
    AgentRegistration,
    AgentRegistrationResponse,
    AgentRole,
    AssignmentResult,
    Task,
    TaskPlan,
    TaskStatus,
    TaskSubmitRequest,
)
from src.orchestrator.dispatcher import Dispatcher
from src.orchestrator.service import handle_worker_result, run_planning_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    orch = OrchestratorConfig()
    gc = GiteaConfig()
    lc = LLMConfig()
    raw = load_yaml_config(orch.config_path)
    yaml_cfg = merge_swarm_yaml(raw)
    metrics = MetricsCollector(orch.metrics_db)
    await metrics.init_db()
    gitea = GiteaClient(gc.url, gc.token, gc.organization)
    llm = SwarmLLM(lc.hf_model, lc.n_ctx, lc.n_threads, lc.idle_timeout_seconds)
    dispatcher = Dispatcher(metrics)

    app.state.orch_cfg = orch
    app.state.yaml_cfg = yaml_cfg
    app.state.gitea_cfg = gc
    app.state.llm_cfg = lc
    app.state.metrics = metrics
    app.state.gitea = gitea
    app.state.llm = llm
    app.state.dispatcher = dispatcher
    app.state.review_counts = {}

    async def idle_loop() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                llm.maybe_unload_if_idle()
            except Exception as exc:
                logger.warning("idle_loop: %s", exc)

    async def health_loop() -> None:
        while True:
            await asyncio.sleep(30)
            try:
                agents = await metrics.get_agents()
                import httpx

                async with httpx.AsyncClient(timeout=5.0) as client:
                    for a in agents:
                        if a.role != AgentRole.DEVELOPER:
                            continue
                        url = f"http://{a.host}:{a.port}/health"
                        try:
                            r = await client.get(url)
                            if r.status_code != 200:
                                await metrics.update_agent_status(a.agent_id, "offline")
                            else:
                                data = r.json()
                                reported = data.get("status", "ok")
                                st = "busy" if reported == "busy" else "idle"
                                await metrics.update_agent_status(a.agent_id, st)
                        except Exception:
                            await metrics.update_agent_status(a.agent_id, "offline")
            except Exception as exc:
                logger.warning("health_loop: %s", exc)

    t1 = asyncio.create_task(idle_loop())
    t2 = asyncio.create_task(health_loop())
    yield
    t1.cancel()
    t2.cancel()
    await gitea.close()
    await dispatcher.close()


app = FastAPI(title="Pi Swarm Orchestrator", lifespan=lifespan)


def _schedule_planning(task_id: str, application: FastAPI) -> None:
    """Run planning in the event loop so errors are logged (BackgroundTasks can hide failures)."""
    st = application.state

    async def _run() -> None:
        try:
            await run_planning_pipeline(
                task_id,
                st.metrics,
                st.gitea,
                st.llm,
                st.orch_cfg,
                st.yaml_cfg,
                st.dispatcher,
            )
        except Exception:
            logger.exception("Unhandled error in planning task_id=%s", task_id)

    asyncio.create_task(_run())


@app.post("/tasks")
async def post_task(req: TaskSubmitRequest, request: Request) -> dict[str, str]:
    metrics: MetricsCollector = request.app.state.metrics
    tid = uuid.uuid4().hex[:12]
    task = Task(
        task_id=tid,
        title=req.title,
        description=req.description,
        repo_name=req.repo_name,
        language=req.language,
        constraints=req.constraints,
        status=TaskStatus.PENDING,
    )
    await metrics.save_task(task)
    _schedule_planning(tid, request.app)
    return {"task_id": tid}


@app.get("/tasks")
async def list_tasks(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    tasks = await request.app.state.metrics.list_tasks(limit)
    return [t.model_dump(mode="json") for t in tasks]


@app.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any] | None:
    t = await request.app.state.metrics.get_task(task_id)
    return t.model_dump(mode="json") if t else None


@app.post("/tasks/{task_id}/plan")
async def replan_task(task_id: str, request: Request) -> dict[str, str]:
    _schedule_planning(task_id, request.app)
    return {"status": "scheduled"}


@app.post("/tasks/{task_id}/manual")
async def manual_plan(task_id: str, plan: TaskPlan, request: Request) -> dict[str, str]:
    metrics: MetricsCollector = request.app.state.metrics
    t = await metrics.get_task(task_id)
    if not t:
        return {"error": "not found"}
    t.plan = plan
    t.status = TaskStatus.IN_PROGRESS
    await metrics.save_task(t)
    request.app.state.dispatcher.set_repo(task_id, plan.repo_name)
    await request.app.state.dispatcher.start_task(t, plan.assignments)
    return {"status": "ok"}


@app.post("/webhooks/gitea")
async def webhook_gitea(request: Request) -> dict[str, str]:
    try:
        payload = await request.json()
    except Exception:
        return {"ok": "false"}
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return {"ok": "true"}
    num = pr.get("number")
    repo = payload.get("repository") or {}
    name = str(repo.get("name", "")) if isinstance(repo, dict) else ""
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    branch = str(head.get("ref", ""))
    if not name or num is None:
        return {"ok": "true"}
    metrics = request.app.state.metrics
    tasks = await metrics.list_tasks(100)
    for t in tasks:
        if not t.plan or t.plan.repo_name != name:
            continue
        asg = next(
            (
                a
                for a in t.plan.assignments
                if branch == a.branch_name or branch.endswith(a.branch_name.split("/")[-1])
            ),
            None,
        )
        if asg is None:
            continue
        res = AssignmentResult(
            agent_id=asg.agent_id,
            task_id=t.task_id,
            branch_name=asg.branch_name,
            pr_number=int(num),
            success=True,
        )
        await handle_worker_result(
            res,
            metrics,
            request.app.state.gitea,
            request.app.state.llm,
            request.app.state.orch_cfg,
            request.app.state.yaml_cfg,
            request.app.state.dispatcher,
            request.app.state.review_counts,
        )
        break
    return {"ok": "true"}


@app.post("/agents/register")
async def register_agent(reg: AgentRegistration, request: Request) -> AgentRegistrationResponse:
    metrics: MetricsCollector = request.app.state.metrics
    node = AgentNode(
        agent_id=reg.agent_id,
        role=AgentRole.DEVELOPER,
        host=reg.host,
        port=reg.port,
        capabilities=reg.capabilities,
    )
    await metrics.register_agent(node)
    gc: GiteaConfig = request.app.state.gitea_cfg
    return AgentRegistrationResponse(ok=True, gitea_url=gc.url, gitea_org=gc.organization)


@app.get("/agents")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    agents = await request.app.state.metrics.get_agents()
    return [a.model_dump(mode="json") for a in agents]


@app.post("/agents/{agent_id}/result")
async def agent_result(agent_id: str, body: AssignmentResult, request: Request) -> dict[str, str]:
    await handle_worker_result(
        body,
        request.app.state.metrics,
        request.app.state.gitea,
        request.app.state.llm,
        request.app.state.orch_cfg,
        request.app.state.yaml_cfg,
        request.app.state.dispatcher,
        request.app.state.review_counts,
    )
    return {"status": "ok"}


@app.get("/metrics")
async def get_metrics(request: Request) -> dict[str, Any]:
    m = await request.app.state.metrics.recent_metrics(50)
    return {"recent": m}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    rows = await request.app.state.metrics.recent_metrics(30)
    agents = await request.app.state.metrics.get_agents()
    agdicts = [a.model_dump() for a in agents]
    return render_dashboard(rows, agdicts)
