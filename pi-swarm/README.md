# Pi Swarm

Distributed coding agents for Raspberry Pi: a **lead orchestrator** (FastAPI on port 8080) and **workers** (FastAPI on port 8000) that use local **llama.cpp** models and **Gitea** for repos, branches, and pull requests.

## Requirements

- Python 3.11+
- `llama-cpp-python`, Hugging Face Hub (for GGUF resolution), SQLite (`aiosqlite`)

Install:

```bash
pip install -r requirements.txt
```

## Configuration

### Environment (Ansible/systemd)

- **Orchestrator:** `GITEA_TOKEN`, `GITEA_API_BASE_URL`, `SWARM_METRICS_DB`, `SWARM_CONFIG_PATH`
- **Worker:** `GITEA_TOKEN`, `GITEA_API_BASE_URL`, `ORCHESTRATOR_URL`, `AGENT_ID`, `HF_MODEL`, `WORKER_ADVERTISE_HOST`

YAML defaults live in `config/swarm_config.yaml` (prompt paths, `webhook_base_url`).

## Run

From the app root (e.g. `/opt/pi-swarm/app`):

```bash
export PYTHONPATH=.
# Lead
python -m uvicorn src.orchestrator.app:app --host 0.0.0.0 --port 8080
# Worker
python -m uvicorn src.worker.app:app --host 0.0.0.0 --port 8000
```

## API (orchestrator)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/tasks` | Submit task (`title`, `description`, `repo_name`, …) |
| GET | `/tasks`, `/tasks/{id}` | List / get task |
| POST | `/tasks/{id}/plan` | Re-run planning |
| POST | `/tasks/{id}/manual` | Submit a full `TaskPlan` JSON (skip LLM planner) |
| POST | `/agents/register` | Worker registration; returns `gitea_url` |
| POST | `/agents/{id}/result` | Worker assignment result |
| POST | `/webhooks/gitea` | Gitea webhook |
| GET | `/dashboard` | HTML metrics dashboard |

## CLI

```bash
PYTHONPATH=. python scripts/submit_task.py --orchestrator http://LEAD:8080 \
  --title "My task" --description "..." --repo my-repo
```

## Tests

```bash
pip install -e '.[dev]'
PYTHONPATH=. pytest tests/
```

Integration tests: `PI_SWARM_INTEGRATION=1 GITEA_API_BASE_URL=... GITEA_TOKEN=... pytest tests/test_integration.py`

## Layout

- `src/config.py` — env + YAML loading
- `src/models.py` — Pydantic models
- `src/llm.py` — `SwarmLLM` (lazy load, idle unload)
- `src/git_ops*.py` — Gitea HTTP client
- `src/orchestrator/` — planner, reviewer, dispatcher, merger, app
- `src/worker/` — coder, executor, app
- `src/metrics/` — SQLite + dashboard

## Scope (MVP)

Syntax validation only for Python; no pytest/ruff in the worker gate. Trusted LAN; no auth between nodes.
