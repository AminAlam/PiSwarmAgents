"""Jinja2 HTML dashboard for orchestrator GET /dashboard."""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import Environment, BaseLoader, select_autoescape

logger = logging.getLogger(__name__)

_DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Pi Swarm — Dashboard</title>
  <style>
    :root { --bg: #0f1419; --fg: #e6edf3; --muted: #8b949e; --accent: #3fb950; --card: #161b22; }
    body { font-family: ui-sans-serif, system-ui, sans-serif; background: var(--bg); color: var(--fg); margin: 0; padding: 2rem; }
    h1 { font-weight: 600; letter-spacing: -0.02em; }
    table { width: 100%; border-collapse: collapse; margin: 1.5rem 0; background: var(--card); border-radius: 8px; overflow: hidden; }
    th, td { text-align: left; padding: 0.65rem 1rem; border-bottom: 1px solid #21262d; }
    th { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; }
    tr:last-child td { border-bottom: none; }
    .bar-wrap { display: flex; align-items: flex-end; gap: 4px; height: 120px; margin-top: 1rem; }
    .bar { flex: 1; background: linear-gradient(180deg, var(--accent), #238636); border-radius: 4px 4px 0 0; min-height: 4px; }
    .muted { color: var(--muted); font-size: 0.85rem; }
    a { color: #58a6ff; text-decoration: none; }
  </style>
</head>
<body>
  <h1>Pi Swarm</h1>
  <p class="muted">Metrics refresh on page reload.</p>

  <h2>Tasks</h2>
  <table>
    <thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Tokens (approx)</th></tr></thead>
    <tbody>
    {% for t in task_rows %}
      <tr>
        <td><code>{{ t.task_id }}</code></td>
        <td>{{ t.title }}</td>
        <td>{{ t.status }}</td>
        <td>{{ t.tokens }}</td>
      </tr>
    {% else %}
      <tr><td colspan="4" class="muted">No tasks yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>

  {% if bars %}
  <h2>Tokens per task</h2>
  <div class="bar-wrap" role="img" aria-label="token bars">
    {% for b in bars %}
      <div class="bar" style="height: {{ b.pct }}%;" title="{{ b.label }}: {{ b.value }}"></div>
    {% endfor %}
  </div>
  {% endif %}

  <h2>Agents</h2>
  <table>
    <thead><tr><th>ID</th><th>Host</th><th>Status</th><th>Task</th></tr></thead>
    <tbody>
    {% for a in agents %}
      <tr>
        <td><code>{{ a.agent_id }}</code></td>
        <td>{{ a.host }}:{{ a.port }}</td>
        <td>{{ a.status }}</td>
        <td>{{ a.current_task_id or "—" }}</td>
      </tr>
    {% else %}
      <tr><td colspan="4" class="muted">No agents registered.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""


def render_dashboard(
    task_rows: list[dict[str, Any]],
    agents: list[dict[str, Any]],
) -> str:
    """Render HTML dashboard."""
    try:
        max_tok = max((int(r.get("tokens") or 0) for r in task_rows), default=0)
        bars: list[dict[str, Any]] = []
        for r in task_rows[:20]:
            v = int(r.get("tokens") or 0)
            pct = int(100 * v / max_tok) if max_tok else 0
            bars.append(
                {
                    "label": str(r.get("task_id", "")),
                    "value": v,
                    "pct": max(pct, 4),
                },
            )
        env = Environment(
            loader=BaseLoader(),
            autoescape=select_autoescape(["html", "xml"]),
        )
        tpl = env.from_string(_DASHBOARD_TEMPLATE)
        return tpl.render(task_rows=task_rows, agents=agents, bars=bars)
    except Exception as exc:
        logger.exception("render_dashboard failed: %s", exc)
        return "<html><body><p>Dashboard error</p></body></html>"
