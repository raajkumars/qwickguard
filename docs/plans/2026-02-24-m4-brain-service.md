# M4: Brain Service + Dashboard - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the QwickGuard Brain - a FastAPI service that receives agent reports, stores metrics in SQLite, escalates complex issues to Claude API, dispatches notifications (GitHub Issues + Slack), generates daily digests, and serves a unified dashboard UI with embedded metrics, audit log viewer, runbook browser, and links to Beszel/Portainer/Dozzle.

**Architecture:** FastAPI in Docker on port 8500. SQLite for 7-day metrics. Jinja2 + HTMX + Chart.js for server-rendered dashboard with 5-minute polling. Claude API (claude-sonnet-4-6) for escalation. GitHub Issues + Slack webhooks for notifications.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, Jinja2, SQLite (aiosqlite), httpx, anthropic SDK, Chart.js (CDN), HTMX (CDN)

**Related Issues:** #15 (epic), #18, #21, #24, #26, #28, #30
**Design Doc:** `docs/plans/2026-02-22-qwickguard-design.md` (Sections 5, 8)

---

## Context

The local agent (M3) is deployed on macmini-devserver, running 5-minute cycles. It POSTs reports to `http://localhost:8500/api/v1/agents/{agent_id}/report`. Currently, the brain doesn't exist, so reports are queued locally at `~/.qwickguard/report_queue/`.

The dashboard replaces 4 separate UIs with a single entry point at `http://macmini-devserver:8500`.

---

## Task 1: Scaffold Brain Service (Issue #18)

**Files:**
- Create: `brain/pyproject.toml`
- Create: `brain/Dockerfile`
- Create: `brain/src/qwickguard_brain/__init__.py`
- Create: `brain/src/qwickguard_brain/main.py`
- Create: `brain/src/qwickguard_brain/config.py`
- Create: `brain/src/qwickguard_brain/storage.py`
- Create: `brain/src/qwickguard_brain/api/__init__.py`
- Create: `brain/src/qwickguard_brain/api/health.py`
- Modify: `docker-compose.yml` (add brain service)

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "qwickguard-brain"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "jinja2>=3.1",
    "aiosqlite>=0.20",
    "httpx>=0.27",
    "anthropic>=0.40",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-mock>=3.12", "httpx>=0.27"]
```

**Step 2: Create Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY brain/ ./
RUN pip install --no-cache-dir -e .
EXPOSE 8500
CMD ["uvicorn", "qwickguard_brain.main:app", "--host", "0.0.0.0", "--port", "8500"]
```

**Step 3: Create config.py**

Settings from environment variables:
- `DATABASE_PATH`: SQLite path (default: `/data/qwickguard.db`)
- `ANTHROPIC_API_KEY`: Claude API key (optional, escalation disabled without it)
- `GITHUB_TOKEN`: GitHub PAT for issue creation (optional)
- `GITHUB_REPO`: repo for issues (default: `raajkumars/qwickguard`)
- `SLACK_WEBHOOK_URL`: Slack webhook (optional)
- `DISCORD_WEBHOOK_URL`: Discord webhook (optional)
- `HEARTBEAT_TIMEOUT_MINUTES`: 15
- `MAX_CLAUDE_CALLS_PER_DAY`: 20
- `DATA_RETENTION_DAYS`: 7

**Step 4: Create storage.py**

SQLite schema:
```sql
CREATE TABLE IF NOT EXISTS agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    hostname TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_reports_agent_ts ON agent_reports(agent_id, timestamp);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    last_report_at TEXT,
    last_status TEXT,
    registered_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    claude_response TEXT,
    actions_recommended TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    channel TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

Functions:
- `init_db()`: create tables
- `store_report(report)`: insert report, update agent last_report
- `get_agent_history(agent_id, hours=168)`: 7-day history
- `get_agents()`: list all agents with status
- `get_recent_reports(agent_id, limit=50)`: recent reports
- `get_recent_actions(agent_id, limit=100)`: extract actions from reports
- `get_recent_notifications(limit=50)`: notification history
- `cleanup_old_data(retention_days=7)`: delete old records
- `store_escalation(...)`: save Claude escalation
- `store_notification(...)`: save notification record

**Step 5: Create main.py**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from .storage import init_db, cleanup_old_data
from .api.health import router as health_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Start background tasks (heartbeat checker, daily cleanup)
    yield

app = FastAPI(title="QwickGuard Brain", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
```

**Step 6: Create api/health.py**

```python
@router.get("/health")
async def health():
    return {"status": "ok", "service": "qwickguard-brain", "version": "0.1.0"}
```

**Step 7: Add brain to docker-compose.yml**

```yaml
  brain:
    build:
      context: .
      dockerfile: brain/Dockerfile
    container_name: qwickguard-brain
    restart: unless-stopped
    ports:
      - "8500:8500"
    volumes:
      - brain_data:/data
      - ./docs/runbooks:/app/runbooks:ro
    environment:
      DATABASE_PATH: /data/qwickguard.db
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      GITHUB_TOKEN: ${GITHUB_TOKEN:-}
      GITHUB_REPO: raajkumars/qwickguard
      SLACK_WEBHOOK_URL: ${SLACK_WEBHOOK_URL:-}
    networks:
      - default
```

Add `brain_data:` to volumes section.

**Step 8: Build and test**

```bash
cd /path/to/qwickguard
docker compose build brain
docker compose up -d brain
curl -sf http://localhost:8500/health
```

Expected: `{"status":"ok","service":"qwickguard-brain"}`

**Step 9: Commit**

```bash
git add brain/ docker-compose.yml
git commit -m "feat: scaffold Brain service (FastAPI, Dockerfile, SQLite)

Closes #18"
```

---

## Task 2: Agent Report Ingestion and Heartbeat Tracking (Issue #21)

**Files:**
- Create: `brain/src/qwickguard_brain/api/agents.py`
- Create: `brain/src/qwickguard_brain/registry.py`
- Modify: `brain/src/qwickguard_brain/main.py` (add router, background task)
- Create: `brain/tests/test_api_agents.py`

**Step 1: Create api/agents.py**

Endpoints:
- `POST /api/v1/agents/{agent_id}/report` - Accept report, store in SQLite, update heartbeat
- `GET /api/v1/agents` - List all registered agents with last heartbeat
- `GET /api/v1/agents/{agent_id}/history` - Metric history (7 days)
- `GET /api/v1/agents/{agent_id}/actions` - Recent actions taken

Request body for report: matches AgentReport from agent models (accept as dict, validate key fields).

**Step 2: Create registry.py**

Background heartbeat checker:
```python
async def heartbeat_checker(interval_seconds=60):
    """Check every minute for agents missing heartbeat > 15 min."""
    while True:
        agents = await get_agents()
        for agent in agents:
            last_report = parse_datetime(agent["last_report_at"])
            if datetime.utcnow() - last_report > timedelta(minutes=15):
                await send_critical_alert(
                    agent_id=agent["agent_id"],
                    title=f"Agent {agent['hostname']} heartbeat missing",
                    body=f"No report received in {minutes_since} minutes",
                )
        await asyncio.sleep(interval_seconds)
```

**Step 3: Wire into main.py**

Add agents router, start heartbeat_checker as background task in lifespan.

**Step 4: Write tests**

- test_post_report: POST valid report, verify 200, verify stored
- test_get_agents: POST report then GET agents, verify listed
- test_get_history: POST multiple reports, verify history returned
- test_heartbeat_missing: mock time, verify alert triggered

**Step 5: Rebuild and test**

```bash
docker compose build brain && docker compose up -d brain
# Send a test report
curl -X POST http://localhost:8500/api/v1/agents/macmini-1/report \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"macmini-1","hostname":"macmini-devserver","timestamp":"2026-02-24T12:00:00Z","metrics":{},"analysis":{"status":"healthy","issues":[],"actions":[],"escalate_to_claude":false},"actions_taken":[]}'
```

**Step 6: Commit**

```bash
git add brain/
git commit -m "feat: add agent report ingestion and heartbeat tracking

Closes #21"
```

---

## Task 3: Claude API Escalation Engine (Issue #24)

**Files:**
- Create: `brain/src/qwickguard_brain/escalation.py`
- Create: `brain/prompts/diagnosis.md`
- Create: `brain/tests/test_escalation.py`

**Step 1: Create prompts/diagnosis.md**

System prompt for Claude escalation:
```markdown
You are QwickGuard, an infrastructure diagnosis engine for {hostname}.

Given the metrics, container status, service health, and Llama's initial analysis,
provide a structured diagnosis.

Respond with JSON:
{
  "severity": "critical" | "warning",
  "diagnosis": "Root cause explanation",
  "recommended_actions": [
    {"action": "action_name", "target": "...", "reason": "..."}
  ],
  "escalation_summary": "Human-readable summary for notification"
}

Only recommend actions from the approved catalog:
restart_container, docker_compose_up, kill_zombies, prune_images,
run_backup, restart_colima, rotate_logs.

NEVER recommend: docker rm, docker volume rm, DROP, DELETE, TRUNCATE.
```

**Step 2: Create escalation.py**

```python
from anthropic import AsyncAnthropic

class EscalationEngine:
    def __init__(self, config):
        self.client = AsyncAnthropic(api_key=config.anthropic_api_key) if config.anthropic_api_key else None
        self.daily_count = {}  # agent_id -> count today
        self.cache = {}  # issue_hash -> (timestamp, response)

    async def escalate(self, agent_id, report, context) -> dict | None:
        if not self.client:
            return None

        # Rate limit: max 20 per day per agent
        today = date.today().isoformat()
        key = f"{agent_id}:{today}"
        if self.daily_count.get(key, 0) >= config.max_claude_calls_per_day:
            return None

        # Cache: same issue within 1 hour = skip
        issue_hash = hash_issues(report["analysis"]["issues"])
        if issue_hash in self.cache:
            cached_ts, cached_resp = self.cache[issue_hash]
            if datetime.utcnow() - cached_ts < timedelta(hours=1):
                return cached_resp

        # Call Claude
        system_prompt = load_diagnosis_prompt(report["hostname"])
        user_message = format_escalation_context(report, context)

        response = await self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        result = parse_diagnosis(response.content[0].text)
        self.daily_count[key] = self.daily_count.get(key, 0) + 1
        self.cache[issue_hash] = (datetime.utcnow(), result)

        await store_escalation(agent_id, report, result)
        return result
```

**Step 3: Wire into report ingestion**

In agents.py POST handler: if report `escalate_to_claude` is True, call escalation engine.

**Step 4: Write tests**

Mock Anthropic client. Test rate limiting, caching, prompt construction, response parsing.

**Step 5: Commit**

```bash
git add brain/
git commit -m "feat: add Claude API escalation engine

claude-sonnet-4-6 for complex diagnosis. Rate limited (20/day/agent),
1-hour issue dedup cache. Structured JSON responses.

Closes #24"
```

---

## Task 4: GitHub Issues + Slack Notifications (Issues #26, #28)

**Files:**
- Create: `brain/src/qwickguard_brain/notifications.py`
- Create: `brain/tests/test_notifications.py`

**Step 1: Create notifications.py**

Two notification channels:

**GitHub Issues:**
```python
async def create_github_issue(config, severity, title, body, labels):
    # Dedup: search for existing open issue with same title
    async with httpx.AsyncClient() as client:
        search = await client.get(
            f"https://api.github.com/search/issues",
            params={"q": f"repo:{config.github_repo} is:open in:title {title}"},
            headers={"Authorization": f"token {config.github_token}"},
        )
        if search.json()["total_count"] > 0:
            # Add comment to existing issue instead
            existing = search.json()["items"][0]
            await add_comment(client, config, existing["number"], body)
            return existing["number"]

        # Create new issue
        resp = await client.post(
            f"https://api.github.com/repos/{config.github_repo}/issues",
            json={"title": title, "body": body, "labels": labels},
            headers={"Authorization": f"token {config.github_token}"},
        )
        return resp.json()["number"]

async def close_github_issue(config, title, comment):
    # Find open issue by title, add resolution comment, close it
```

**Slack/Discord webhooks:**
```python
async def send_slack_alert(webhook_url, severity, title, body, hostname):
    color = {"critical": "#FF0000", "warning": "#FFA500", "info": "#36A64F"}
    payload = {
        "attachments": [{
            "color": color.get(severity, "#808080"),
            "title": f"[{hostname}] {title}",
            "text": body,
            "footer": "QwickGuard",
            "ts": int(time.time()),
        }]
    }
    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json=payload)
```

**Notification dispatcher:**
```python
async def dispatch_notification(config, agent_id, severity, title, body):
    """Route notification to appropriate channels based on severity."""
    # Always store in DB
    await store_notification(agent_id, severity, title, body, "internal")

    # GitHub Issues: critical and warning only
    if severity in ("critical", "warning") and config.github_token:
        labels = [severity, "auto-generated"]
        issue_num = await create_github_issue(config, severity, title, body, labels)
        await store_notification(agent_id, severity, title, body, "github", str(issue_num))

    # Slack: critical and warning only
    if severity in ("critical", "warning") and config.slack_webhook_url:
        await send_slack_alert(config.slack_webhook_url, severity, title, body, hostname)
        await store_notification(agent_id, severity, title, body, "slack")
```

**Step 2: Wire into report ingestion and heartbeat checker**

- Missing heartbeat → dispatch critical notification
- Agent reports critical status → dispatch warning/critical notification
- Escalation result → dispatch with Claude's summary

**Step 3: Write tests**

Mock httpx. Test dedup, issue creation, Slack formatting, severity routing.

**Step 4: Commit**

```bash
git add brain/
git commit -m "feat: add GitHub Issues + Slack/Discord notifications

Issue dedup by title search. Auto-close on resolution. Severity-based routing.
Slack attachments with color coding.

Closes #26
Closes #28"
```

---

## Task 5: Daily Digest (Issue #30)

**Files:**
- Create: `brain/src/qwickguard_brain/digest.py`
- Create: `brain/prompts/daily_digest.md`
- Create: `brain/tests/test_digest.py`

**Step 1: Create prompts/daily_digest.md**

```markdown
Summarize the last 24 hours of infrastructure monitoring for {hostname}.

Data provided:
- Metric trends (CPU, RAM, disk averages and peaks)
- Actions taken by the agent (restarts, cleanups)
- Incidents and escalations
- Backup status
- Service uptime

Generate a concise, human-readable daily report. Include:
1. Overall health assessment (one sentence)
2. Key metrics summary (brief)
3. Notable incidents (if any)
4. Actions taken (count and summary)
5. Backup status
6. Recommendations (if any)

Keep it under 300 words. Use markdown formatting.
```

**Step 2: Create digest.py**

```python
async def generate_daily_digest(config):
    """Generate and dispatch daily digest for all agents."""
    agents = await get_agents()
    for agent in agents:
        # Aggregate 24h data
        reports = await get_agent_history(agent["agent_id"], hours=24)
        actions = await get_recent_actions(agent["agent_id"], hours=24)
        escalations = await get_recent_escalations(agent["agent_id"], hours=24)

        # Build context
        context = format_digest_context(agent, reports, actions, escalations)

        # Generate summary via Claude (if available) or template
        if config.anthropic_api_key:
            summary = await claude_digest(config, agent["hostname"], context)
        else:
            summary = template_digest(agent, reports, actions, escalations)

        # Dispatch: GitHub Issue + Slack
        title = f"Daily Digest: {agent['hostname']} - {date.today()}"
        await dispatch_notification(config, agent["agent_id"], "info", title, summary)

async def schedule_daily_digest(config, hour=8, minute=0):
    """Background task: run digest at configured time daily."""
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        await generate_daily_digest(config)
```

**Step 3: Wire into main.py lifespan**

Start `schedule_daily_digest` as background task.

**Step 4: Write tests**

Test digest aggregation, template fallback, Claude integration (mock).

**Step 5: Commit**

```bash
git add brain/
git commit -m "feat: add daily digest report generation

Aggregates 24h metrics, actions, incidents. Claude summary when available,
template fallback otherwise. Scheduled daily at 8:00 AM.

Closes #30"
```

---

## Task 6: Dashboard UI - Base Layout and Overview Page

**Files:**
- Create: `brain/src/qwickguard_brain/templates/base.html`
- Create: `brain/src/qwickguard_brain/templates/dashboard.html`
- Create: `brain/src/qwickguard_brain/templates/partials/system_status.html`
- Create: `brain/src/qwickguard_brain/templates/partials/container_status.html`
- Create: `brain/src/qwickguard_brain/templates/partials/recent_actions.html`
- Create: `brain/src/qwickguard_brain/api/dashboard.py`
- Modify: `brain/src/qwickguard_brain/main.py` (add template config, dashboard router)

**Step 1: Create base.html**

Dark theme layout with sidebar navigation:
- Sidebar: Dashboard, Metrics, Audit Log, Runbooks, Notifications, External Links (Beszel/Portainer/Dozzle)
- Header: QwickGuard logo, agent status indicator (green/yellow/red dot)
- Content area: main content
- CSS: dark theme with CSS variables (bg: #0f0f0f, cards: #1a1a2e, accent: #16c784)
- Load HTMX and Chart.js from CDN
- All CSS inline in base.html (no external files needed)

**Step 2: Create dashboard.html (overview page)**

Grid layout:
- **Agent Status Card**: hostname, last report time, current status (healthy/warning/critical), uptime
- **System Metrics Cards**: CPU%, RAM%, Disk% with colored progress bars (green < warning < red)
- **Container Status Table**: name, status, health, restart count, uptime - color coded
- **Service Health Table**: name, url, healthy/unhealthy, response time
- **Recent Actions Feed**: last 10 actions with timestamp, action, target, result (success/failed badge)
- **Quick Links**: Beszel, Portainer, Dozzle buttons

HTMX polling: `hx-get="/dashboard/partials/overview" hx-trigger="every 300s"` to refresh data.

**Step 3: Create api/dashboard.py**

```python
@router.get("/")
@router.get("/dashboard")
async def dashboard(request: Request):
    agents = await get_agents()
    agent = agents[0] if agents else None
    latest = await get_latest_report(agent["agent_id"]) if agent else None
    actions = await get_recent_actions(agent["agent_id"], limit=10) if agent else []
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "agent": agent, "latest": latest, "actions": actions,
    })

@router.get("/dashboard/partials/overview")
async def overview_partial(request: Request):
    # Same data, return only the content partial (for HTMX swap)
    ...
```

**Step 4: Wire into main.py**

Configure Jinja2 templates directory, add dashboard router, mount at root.

**Step 5: Rebuild and verify**

```bash
docker compose build brain && docker compose up -d brain
curl -sf http://localhost:8500/dashboard | head -20
```

**Step 6: Commit**

```bash
git add brain/
git commit -m "feat: add dashboard UI with dark theme overview page

Server-rendered Jinja2 + HTMX polling + Chart.js.
System metrics, container status, recent actions, quick links."
```

---

## Task 7: Dashboard - Metrics Charts Page

**Files:**
- Create: `brain/src/qwickguard_brain/templates/metrics.html`
- Create: `brain/src/qwickguard_brain/templates/partials/metrics_charts.html`
- Modify: `brain/src/qwickguard_brain/api/dashboard.py` (add metrics endpoint)

**Step 1: Create metrics.html**

Charts page with:
- **CPU History** (line chart, 24h/7d toggle): CPU% over time
- **RAM History** (line chart): RAM% over time with warning/critical threshold lines
- **Disk History** (line chart): Disk% over time
- **Load Average** (line chart): 1m, 5m, 15m load
- **Container Restart Count** (bar chart): restarts per container in last 24h
- **Service Response Times** (line chart): response time per service

Use Chart.js with dark theme colors. Data passed as JSON from FastAPI.
Time range selector: 1h, 6h, 24h, 7d buttons that trigger HTMX requests with different range params.

**Step 2: Add API endpoint**

```python
@router.get("/metrics")
async def metrics_page(request: Request, range: str = "24h"):
    history = await get_agent_history(agent_id, hours=parse_range(range))
    chart_data = prepare_chart_data(history)
    return templates.TemplateResponse("metrics.html", {
        "request": request, "chart_data": chart_data, "range": range,
    })
```

**Step 3: Commit**

```bash
git add brain/
git commit -m "feat: add metrics charts page with Chart.js

CPU, RAM, disk, load, container restarts, service response times.
Time range selector: 1h, 6h, 24h, 7d."
```

---

## Task 8: Dashboard - Audit Log Viewer

**Files:**
- Create: `brain/src/qwickguard_brain/templates/audit.html`
- Modify: `brain/src/qwickguard_brain/api/dashboard.py`

**Step 1: Create audit.html**

Filterable table of all actions:
- Columns: Timestamp, Action, Target, Reason, Decided By, Result, Error
- Result badges: green (success), red (failed), yellow (cooldown), grey (rejected)
- Filters: action type dropdown, result dropdown, date range
- HTMX: filter changes trigger partial reload
- Pagination: 50 items per page, HTMX load-more button

**Step 2: Add API endpoints**

```python
@router.get("/audit")
async def audit_page(request: Request, action: str = None, result: str = None, page: int = 1):
    actions = await get_filtered_actions(action=action, result=result, page=page, per_page=50)
    return templates.TemplateResponse("audit.html", {
        "request": request, "actions": actions, "filters": {"action": action, "result": result},
    })
```

**Step 3: Commit**

```bash
git add brain/
git commit -m "feat: add audit log viewer with filters and pagination"
```

---

## Task 9: Dashboard - Runbook Browser

**Files:**
- Create: `brain/src/qwickguard_brain/templates/runbooks.html`
- Create: `brain/src/qwickguard_brain/templates/runbook_detail.html`
- Modify: `brain/src/qwickguard_brain/api/dashboard.py`

**Step 1: Create runbooks.html**

List all runbooks from `/app/runbooks/` (mounted from `docs/runbooks/`):
- Card per runbook: title (from H1), date verified, severity
- Click to view full runbook

**Step 2: Create runbook_detail.html**

Render markdown runbook as HTML:
- Use Python `markdown` library to convert .md to HTML
- Syntax highlight code blocks
- Dark theme styling for rendered markdown

Add `markdown` to brain dependencies.

**Step 3: Add API endpoints**

```python
@router.get("/runbooks")
async def runbooks_list(request: Request):
    runbooks = scan_runbook_directory("/app/runbooks/")
    return templates.TemplateResponse("runbooks.html", {"request": request, "runbooks": runbooks})

@router.get("/runbooks/{filename}")
async def runbook_detail(request: Request, filename: str):
    content = read_runbook(f"/app/runbooks/{filename}")
    html = markdown.markdown(content, extensions=["fenced_code", "tables"])
    return templates.TemplateResponse("runbook_detail.html", {
        "request": request, "content": html, "filename": filename,
    })
```

**Step 4: Commit**

```bash
git add brain/
git commit -m "feat: add runbook browser with markdown rendering"
```

---

## Task 10: Dashboard - Notification History and Escalation Log

**Files:**
- Create: `brain/src/qwickguard_brain/templates/notifications.html`
- Modify: `brain/src/qwickguard_brain/api/dashboard.py`

**Step 1: Create notifications.html**

Two sections:

**Notifications:**
- Table: timestamp, severity (badge), title, channel (GitHub/Slack/internal), link
- Filter by severity, channel

**Escalations:**
- Table: timestamp, trigger reason, Claude's diagnosis, recommended actions, status
- Expandable rows showing full Claude response

**Step 2: Add API endpoints**

```python
@router.get("/notifications")
async def notifications_page(request: Request, severity: str = None):
    notifications = await get_recent_notifications(limit=100, severity=severity)
    escalations = await get_recent_escalations(limit=50)
    return templates.TemplateResponse("notifications.html", {
        "request": request, "notifications": notifications, "escalations": escalations,
    })
```

**Step 3: Commit**

```bash
git add brain/
git commit -m "feat: add notification history and escalation log viewer"
```

---

## Task 11: Deploy and Verify

**Step 1: Push and deploy**

```bash
git push origin main
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d --build brain'
```

**Step 2: Verify all endpoints**

```bash
ssh macmini-devserver 'curl -sf http://localhost:8500/health | python3 -m json.tool'
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8500/dashboard'
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8500/metrics'
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8500/audit'
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8500/runbooks'
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8500/notifications'
```

**Step 3: Verify agent reports flowing**

Wait 5 minutes for next agent cycle, then:
```bash
ssh macmini-devserver 'curl -sf http://localhost:8500/api/v1/agents | python3 -m json.tool'
```

Should show macmini-1 with recent heartbeat.

**Step 4: Verify queued reports replayed**

The agent's reporter.py replays queued reports on next successful connection. Check:
```bash
ssh macmini-devserver 'ls ~/.qwickguard/report_queue/ | wc -l'
```

Should be 0 (all replayed).

**Step 5: Update README, close issues**

```bash
gh issue close 15 -c "M4 complete"
gh issue close 18 -c "Closed via M4"
gh issue close 21 -c "Closed via M4"
gh issue close 24 -c "Closed via M4"
gh issue close 26 -c "Closed via M4"
gh issue close 28 -c "Closed via M4"
gh issue close 30 -c "Closed via M4"
```

**Step 6: Commit and push**

```bash
git add README.md
git commit -m "docs: update README with M4 brain service + dashboard status"
git push origin main
```

---

## Summary: M4 Task Execution Order

| # | Task | Issue | Priority |
|---|------|-------|----------|
| 1 | Scaffold Brain (FastAPI, Docker, SQLite) | #18 | High |
| 2 | Agent report ingestion + heartbeat | #21 | High |
| 3 | Claude API escalation engine | #24 | High |
| 4 | GitHub Issues + Slack notifications | #26, #28 | High |
| 5 | Daily digest generation | #30 | Medium |
| 6 | Dashboard: base layout + overview | - | High |
| 7 | Dashboard: metrics charts | - | High |
| 8 | Dashboard: audit log viewer | - | Medium |
| 9 | Dashboard: runbook browser | - | Medium |
| 10 | Dashboard: notifications + escalations | - | Medium |
| 11 | Deploy and verify | - | High |

**Estimated total: ~24 hours**

After M4, macmini-devserver has: a unified dashboard at `http://macmini-devserver:8500` with metrics charts, audit log, runbook browser, notification history. Agent reports flow to brain, complex issues escalate to Claude, alerts go to GitHub Issues + Slack.
