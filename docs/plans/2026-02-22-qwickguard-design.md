# QwickGuard Design Document

**Date:** 2026-02-22
**Status:** Approved
**Project:** https://github.com/users/raajkumars/projects/19
**Repo:** https://github.com/raajkumars/qwickguard

---

## 1. Problem Statement

macmini-devserver is critical infrastructure running:
- 7 Docker containers via Colima (qwickbrain stack + faabzi stack)
- 3 GitHub Actions runners (CI/CD)
- qwickai compute worker (embeddings, Llama inference)
- Ollama (local LLM serving)
- Active development servers (faabzi, work-macha)

Current risks:
- **No database backups.** A container deletion wipes production data permanently.
- **Dangerous cron.** Weekly `docker system prune -af --volumes` can destroy data volumes.
- **No monitoring.** All diagnostics require SSH + manual commands.
- **No self-healing.** Crashed services stay down until manually detected and restarted.
- **No audit trail.** Actions taken on the server are not logged.

Recent incident: Docker container accidentally deleted, wiping production database with no recovery path.

---

## 2. Solution Overview

QwickGuard is an AI-powered self-healing infrastructure agent. Two components:

1. **Local Agent** - Python process on each guarded server. Collects metrics, analyzes via Llama (through existing qwickai compute worker), takes autonomous healing actions, reports to brain.

2. **Brain (Hub)** - FastAPI service in Docker. Receives agent reports, escalates to Claude API for complex diagnosis, dispatches notifications (GitHub Issues + Slack/Discord).

Supporting monitoring UIs: Beszel (metrics), Portainer CE (containers), Dozzle (logs).

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      macmini-devserver                           │
│                                                                  │
│  ┌────────────────────┐     ┌──────────────────────────────────┐│
│  │ QwickGuard Agent   │     │ Monitoring Stack (Docker)        ││
│  │ (Python, host)     │     │                                  ││
│  │                    │     │ ┌────────────┐ ┌──────────────┐  ││
│  │ Collectors:        │     │ │ Beszel Hub │ │ Portainer CE │  ││
│  │ - System (psutil)  │     │ │ :8090      │ │ :9000        │  ││
│  │ - Docker           │     │ └────────────┘ └──────────────┘  ││
│  │ - Services (HTTP)  │     │ ┌────────────┐ ┌──────────────┐  ││
│  │ - Processes        │     │ │ Dozzle     │ │ socket-proxy │  ││
│  │                    │     │ │ :8888      │ │ :2375 (local)│  ││
│  │ Analyzer:          │     │ └────────────┘ └──────────────┘  ││
│  │ → Compute Worker   │     │ ┌────────────┐ ┌──────────────┐  ││
│  │   :8001 (Llama)    │     │ │ Beszel     │ │ QwickGuard   │  ││
│  │                    │     │ │ Agent      │ │ Brain :8500  │  ││
│  │ Healer:            │     │ └────────────┘ └──────────────┘  ││
│  │ - Action catalog   │     └──────────────────────────────────┘│
│  │ - Cooldowns        │                                         │
│  │ - Audit log        │     Existing Services:                  │
│  └────────┬───────────┘     - 7 Docker containers (qwickbrain) │
│           │                 - 3 GitHub runners                  │
│           │ POST /report    - Compute worker (:8001)            │
│           └────────────►    - Ollama (:11434)                   │
│                             - Dev servers (faabzi, work-macha)  │
└──────────────────────────────────────────────────────────────────┘
            │                           │
            ▼                           ▼
     ┌──────────────┐          ┌──────────────┐
     │ GitHub Issues│          │ Slack/Discord│
     │ (audit trail)│          │ (real-time)  │
     └──────────────┘          └──────────────┘
```

### Design Principle: Split Architecture

The local agent is fully autonomous for healing. It does not depend on the brain or network to restart crashed containers, kill zombies, or run backups.

The brain adds intelligence (Claude API) and visibility (notifications, dashboards). It runs in Docker alongside monitoring tools. Initially on macmini, movable to a VPS via Tailscale by changing `brain_url` in agent config.

If the brain goes down: local agent keeps self-healing. Dashboards and notifications are temporarily unavailable.
If the network goes down: local agent operates independently.
If macmini goes down: brain detects missing heartbeat and alerts via Slack.

---

## 4. Local Agent

### 4.1 Runtime

- **Language:** Python 3.11+
- **Process management:** macOS LaunchAgent (`com.qwickapps.qwickguard-agent.plist`)
- **Location:** `~/.qwickguard/` on each guarded server
- **Loop interval:** 5 minutes

### 4.2 Core Loop

```
1. COLLECT metrics
   - System: CPU, RAM, disk, load, open files (psutil)
   - Docker: container status, health, restarts, resource usage
   - Services: HTTP health endpoints (configurable per server)
   - Processes: GitHub runner status, zombie detection

2. ANALYZE with Llama (via compute worker)
   - POST http://localhost:8001/api/infer
   - Tiered model selection:
     - Routine: llama3.2:3b (hot, always loaded, fast)
     - Warning: llama-3.1-8b (warm, loads on demand)
     - Deep: qwen-2.5-14b (warm, loads on demand)
   - Structured JSON output: status, issues, actions, escalate_to_claude
   - Fallback: threshold-based rules if compute worker unavailable

3. ACT autonomously (safe actions only)
   - Execute actions from allowlist
   - Enforce cooldown periods
   - Log every action to audit trail

4. REPORT to brain
   - POST /api/v1/agents/{agent_id}/report
   - Include: metrics, actions taken, escalation requests
   - Queue locally if brain unreachable, replay on reconnect
```

### 4.3 Compute Worker Integration

The existing qwickai compute worker (products/qwickbrain-compute-worker) runs on macmini at port 8001. QwickGuard calls it the same way QwickBrain does:

```python
response = httpx.post("http://localhost:8001/api/infer", json={
    "model": "llama3.2:3b",
    "type": "completion",
    "input": {
        "prompt": build_analysis_prompt(metrics),
        "maxTokens": 512,
        "temperature": 0.1
    }
})
```

Model selection by severity:

| Severity | Model | Loaded | Latency |
|----------|-------|--------|---------|
| Routine | llama3.2:3b | Hot (always) | <2s |
| Warning | llama-3.1-8b | Warm (on-demand) | ~10s first call |
| Critical | qwen-2.5-14b | Warm (on-demand) | ~15s first call |
| Escalation | claude-sonnet-4-6 | Cloud API | ~3s |

### 4.4 Action Catalog

Only these actions can be executed. No arbitrary shell commands.

```python
ACTION_CATALOG = {
    "restart_container":   "docker restart {name}",
    "docker_compose_up":   "docker compose -f {compose_file} up -d",
    "kill_zombies":        "pkill -9 -f '{pattern}'",
    "prune_images":        "docker system prune -af",
    "run_backup":          "~/.qwickguard/scripts/backup.sh",
    "restart_colima":      "colima restart",
    "rotate_logs":         "find {path} -name '*.log' -mtime +7 -delete",
}

# NEVER allowed (not in catalog, explicitly rejected):
# - docker rm, docker volume rm
# - docker system prune --volumes
# - Any database DROP/DELETE/TRUNCATE
# - Any git operations
# - Any deployment operations
# - Any config file modifications
```

### 4.5 Cooldown Periods

| Action | Cooldown | On Exceed |
|--------|----------|-----------|
| restart_container (same) | Max 3 in 30 min | Escalate to brain |
| prune_images | Max 1 per day | Skip |
| restart_colima | Max 1 per hour | Escalate to brain |
| kill_zombies | Max 5 per hour | Skip |
| run_backup | Max 1 per hour | Skip |

### 4.6 Autonomous Action Boundaries

| Action | Autonomous | Needs Escalation |
|--------|-----------|-----------------|
| Restart unhealthy container | Yes | If crash-looping (>3 in 30 min) |
| Kill zombie processes | Yes | Never |
| docker system prune (no --volumes) | Yes | Never |
| Delete old logs/temp files | Yes | Never |
| Run pg_dump backup | Yes | Never |
| Restart Colima | Yes | If failed twice |
| docker compose up -d (bring back missing) | Yes | Never |
| Anything touching volumes/data | **No** | Always |
| Config changes | **No** | Always |
| Deployments | **No** | Always |

---

## 5. Brain Service

### 5.1 Runtime

- **Language:** Python 3.11+ (FastAPI)
- **Deployment:** Docker container on port 8500
- **Storage:** SQLite (embedded, no extra DB container)
- **Data retention:** 7-day rolling window

### 5.2 API

```
POST /api/v1/agents/{agent_id}/report    # Accept agent report
GET  /api/v1/agents                      # List registered agents
GET  /api/v1/agents/{agent_id}/history   # Metric history (7 days)
GET  /health                             # Brain health check
```

### 5.3 Agent Registry

- Tracks last report timestamp per agent
- Background task checks every minute for missing heartbeats
- Missing >15 minutes = critical alert to Slack + GitHub Issue

### 5.4 Claude API Escalation

Triggered when:
- Local Llama analysis returns `escalate_to_claude: true`
- Container crash-looping (>3 restarts in 30 min)
- Multiple services down simultaneously
- Disk >85% with no obvious cleanup targets

Context sent to Claude:
- Recent metrics (last 30 minutes)
- Container logs (recent errors)
- Actions already taken
- Llama's analysis
- Server configuration (known services, expected behavior)

Cost controls:
- Max 20 Claude calls per day per agent
- Cache: same issue within 1 hour = skip re-analysis
- Model: claude-sonnet-4-6 (cost-effective for structured analysis)
- Estimated cost: ~$0.05/day at 5 escalations

### 5.5 Notifications

| Severity | GitHub Issue | Slack | Audit Log |
|----------|-------------|-------|-----------|
| Critical (service down, data risk) | Immediately | Immediately | Yes |
| Warning (disk >80%, crash loop) | Create issue | Send alert | Yes |
| Info (routine restart, cleanup) | No | No | Yes |
| Daily digest | Create issue | Send summary | Yes |

GitHub Issues:
- Deduplication: search for existing open issue with same title
- Auto-close: add comment and close when problem resolves
- Labels: critical, warning, info, auto-resolved, daily-digest

### 5.6 Daily Digest

Generated once daily at 8:00 AM:
1. Aggregate 24h: metrics trends, actions taken, incidents, backup status
2. Send to Claude with digest prompt for human-readable summary
3. Create GitHub Issue with label "daily-digest"
4. Send to Slack as formatted message

---

## 6. Backup System

### 6.1 Schedule

| Target | Schedule | Retention | Location |
|--------|----------|-----------|----------|
| faabzi-postgres (all DBs) | Every 6 hours | 14 days | ~/.qwickguard/backups/faabzi-postgres/ |
| qwickbrain-postgres (all DBs) | Every 6 hours | 14 days | ~/.qwickguard/backups/qwickbrain-postgres/ |
| Docker volume snapshot | Daily 2 AM | 30 days | ~/.qwickguard/backups/volumes/ |

### 6.2 Backup Commands

```bash
# faabzi-postgres
docker exec faabzi-postgres pg_dumpall -U postgres \
  | gzip > ~/.qwickguard/backups/faabzi-postgres/$(date +%Y-%m-%d_%H%M).sql.gz

# qwickbrain-postgres
docker exec qwickbrain-postgres pg_dumpall -U qwickbrain \
  | gzip > ~/.qwickguard/backups/qwickbrain-postgres/$(date +%Y-%m-%d_%H%M).sql.gz
```

### 6.3 Verification

- Post-backup: verify file exists and size > minimum threshold
- Weekly: `gunzip -t` on latest backup (gzip integrity)
- Weekly: `pg_restore --list` on latest backup (SQL structure)
- Agent alerts if latest backup is older than 12 hours

### 6.4 Fix Dangerous Cron

**Current:** `docker system prune -af --volumes` (Sunday 3 AM)
**Replace with:** `docker system prune -af --filter "until=48h"` (no --volumes, only old images)

---

## 7. Monitoring Portal

All UIs accessible via Tailscale at `http://macmini-devserver:<port>`.

### 7.1 Stack

| Tool | Port | Purpose | Docker Socket |
|------|------|---------|---------------|
| docker-socket-proxy | 2375 (localhost) | Read-only Docker API for monitoring tools | Direct (read-only) |
| Beszel Hub | 8090 | Historical metrics, Docker stats, alerts | Via agent |
| Beszel Agent | 45876 | Collects system + Docker metrics | Direct (read-only) |
| Portainer CE | 9000 | Container management UI | Via socket-proxy |
| Dozzle | 8888 | Real-time log viewer | Via socket-proxy |
| QwickGuard Brain | 8500 | Agent reports, escalation, notifications | None |

### 7.2 Docker Socket Protection

docker-socket-proxy configuration:
- Read operations enabled: CONTAINERS, SERVICES, NETWORKS, VOLUMES, IMAGES, INFO
- Write operations disabled: POST=0, DELETE=0
- Bound to 127.0.0.1 only (not exposed externally)

GitHub runners, E2E agents, and docker CLI continue using the real Docker socket directly. The proxy only governs monitoring tool access.

### 7.3 Colima Socket

Colima creates its socket at `~/.colima/default/docker.sock`. Create symlink:
```bash
sudo ln -sf $HOME/.colima/default/docker.sock /var/run/docker.sock
```

---

## 8. Safety Rails

### 8.1 Action Allowlist

Only actions from ACTION_CATALOG (Section 4.4) can be executed. Any action not in the catalog raises `ActionNotAllowedError`. Parameters are validated against known container names and patterns.

### 8.2 Shell Injection Prevention

No string interpolation in commands. Container names validated against config. Patterns validated against allowlist.

### 8.3 Audit Trail

Every action logged to `~/.qwickguard/logs/audit.log`:
```json
{
  "timestamp": "2026-02-22T10:05:00Z",
  "action": "restart_container",
  "target": "qwickbrain-server",
  "reason": "Health check failed 3 consecutive times",
  "decided_by": "llama3.2:3b",
  "result": "success",
  "container_healthy_after": true
}
```

### 8.4 Dead Man's Switch

Two independent layers:
1. **Brain-side:** If agent misses 3 reports (15 min), alert via Slack + GitHub Issue
2. **Host-side:** Simple cron on macmini (independent of agent) checks agent process is running. If dead, sends curl to Slack webhook directly. Works even if both agent and brain are down.

---

## 9. Project Structure

```
qwickguard/
├── README.md
├── docker-compose.yml              # Monitoring stack + brain
├── .env.example
│
├── agent/                           # Local agent (host process)
│   ├── pyproject.toml
│   ├── src/qwickguard_agent/
│   │   ├── __init__.py
│   │   ├── main.py                  # Entry point, scheduling loop
│   │   ├── config.py                # Server config loading
│   │   ├── collectors/
│   │   │   ├── __init__.py
│   │   │   ├── system.py            # CPU, RAM, disk (psutil)
│   │   │   ├── docker.py            # Container status, health
│   │   │   ├── services.py          # HTTP health checks
│   │   │   └── processes.py         # Runner status, zombies
│   │   ├── analyzer.py              # Compute worker integration
│   │   ├── healer.py                # Action catalog, execution
│   │   ├── reporter.py              # Report to brain API
│   │   └── models.py                # Pydantic models
│   ├── scripts/
│   │   ├── backup.sh                # pg_dump backup script
│   │   ├── install-agent.sh         # Setup LaunchAgent + cron
│   │   └── uninstall-agent.sh
│   ├── templates/
│   │   └── com.qwickapps.qwickguard-agent.plist
│   └── tests/
│
├── brain/                           # Hub service (Docker)
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── src/qwickguard_brain/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── agents.py            # Agent report endpoints
│   │   │   └── health.py
│   │   ├── escalation.py            # Claude API integration
│   │   ├── notifications.py         # GitHub Issues + Slack
│   │   ├── registry.py              # Agent heartbeat tracking
│   │   ├── digest.py                # Daily digest generation
│   │   └── storage.py               # SQLite metrics store
│   ├── prompts/
│   │   ├── diagnosis.md             # Claude system prompt
│   │   └── daily_digest.md
│   └── tests/
│
├── configs/
│   └── macmini-devserver.yaml       # Server-specific config
│
└── docs/
    └── plans/
        └── 2026-02-22-qwickguard-design.md
```

---

## 10. Server Configuration

```yaml
# configs/macmini-devserver.yaml
hostname: macmini-devserver
agent_id: macmini-1
brain_url: http://localhost:8500  # change to VPS tailscale IP when split
compute_worker_url: http://localhost:8001

check_interval_seconds: 300  # 5 minutes

thresholds:
  cpu_warning: 80
  cpu_critical: 95
  ram_warning: 85
  ram_critical: 95
  disk_warning: 80
  disk_critical: 90

containers:
  - name: faabzi-postgres
    critical: true
    compose_file: null  # standalone
  - name: faabzi-supertokens
    critical: false
    compose_file: null
  - name: qwickbrain-server
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml
  - name: qwickbrain-node
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml
  - name: qwickbrain-postgres
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml
  - name: qwickbrain-redis
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml
  - name: qwickbrain-qdrant
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml
  - name: qwickbrain-neo4j
    critical: true
    compose_file: ~/Projects/qwickbrain/docker-compose.yml

services:
  - name: compute-worker
    url: http://localhost:8001/health
    critical: true
  - name: ollama
    url: http://localhost:11434/api/tags
    critical: false
  - name: gateway-faabzi
    url: http://localhost:3300/qapi/health
    critical: false
  - name: payload-faabzi
    url: http://localhost:3302/api/health
    critical: false

backups:
  - name: faabzi-postgres
    container: faabzi-postgres
    command: "pg_dumpall -U postgres"
    schedule: "0 */6 * * *"
    retention_days: 14
  - name: qwickbrain-postgres
    container: qwickbrain-postgres
    command: "pg_dumpall -U qwickbrain"
    schedule: "0 */6 * * *"
    retention_days: 14

github_runners:
  - ~/actions-runner
  - ~/actions-runner-2
  - ~/actions-runner-3

zombie_patterns:
  - "qwickapps-test-"
  - "tsx watch.*control-panel"
  - "next dev"
```

---

## 11. Implementation Milestones

| Milestone | Scope | Est. Hours | Due |
|-----------|-------|-----------|-----|
| M1: Protection Foundation | Backups, fix cron, restart policies, healthchecks | 8h | Mar 8 |
| M2: Monitoring Portal | Beszel, Portainer, Dozzle, docker-compose | 8h | Mar 15 |
| M3: Local Agent | Python agent, collectors, Llama analysis, healer | 20h | Mar 29 |
| M4: Brain Service | FastAPI hub, Claude escalation, notifications | 19h | Apr 12 |
| M5: Production Hardening | Safety rails, testing, multi-server, docs | 12h | Apr 26 |

**Total estimated effort: ~67 hours**

---

## 12. Resource Overhead

| Component | RAM | CPU | Disk |
|-----------|-----|-----|------|
| docker-socket-proxy | ~5 MB | Negligible | Negligible |
| Beszel Hub | ~23 MB | Negligible | ~100 MB (metrics DB) |
| Beszel Agent | ~6 MB | Negligible | Negligible |
| Portainer CE | ~50 MB | Negligible | ~50 MB (state) |
| Dozzle | ~15 MB | Negligible | None (no persistence) |
| QwickGuard Brain | ~50 MB | Negligible | ~50 MB (SQLite) |
| QwickGuard Agent | ~40 MB | Light (5-min bursts) | ~20 MB (logs) |
| **Total** | **~189 MB** | **Minimal** | **~220 MB** |

On a 24 GB RAM server, this is <1% overhead.

---

## 13. Future Considerations

- **Multi-server:** Deploy agent to server1-qwickforge, server2-qwickforge via same pattern
- **VPS split:** Move brain + monitoring UIs to dedicated VPS, agents communicate via Tailscale
- **Off-site backups:** Add S3/R2 upload for database backups (disaster recovery)
- **Claude Code integration:** QwickGuard could spawn Claude Code sessions for complex fixes
- **Stateful CLI jobs:** Leverage qwickai orchestrator's planned stateful CLI worker for automated code fixes
