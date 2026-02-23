# QwickGuard

AI-powered self-healing infrastructure agent for macmini-devserver (and future servers).

## What It Does

- **Automated backups** - pg_dump every 6 hours with 14-day retention and integrity verification
- **Container monitoring** - healthchecks, restart policies, and crash detection
- **Monitoring portal** - Beszel (metrics), Portainer CE (containers), Dozzle (logs)
- **Self-healing** - autonomous diagnosis via Llama, escalation to Claude API for complex issues (M3+)
- **Notifications** - GitHub Issues (audit trail) + Slack/Discord (real-time alerts) (M4+)

## Architecture

```
Local Agent (Python, host)     Brain Hub (FastAPI, Docker)
  - System collectors            - Agent registry
  - Docker collectors            - Claude escalation
  - Llama analysis               - Notifications
  - Autonomous healer            - Daily digests
  - Reports to brain             - SQLite metrics store
         │                              │
         └──── Docker Socket Proxy ─────┘
               (read-only API)
```

## Monitoring Portal

All accessible via Tailscale at `http://macmini-devserver:<port>`:

| Tool | Port | Purpose | RAM |
|------|------|---------|-----|
| Beszel | 8090 | System metrics, Docker stats, alerts | ~17MB |
| Portainer | 9000 | Container management UI (read-only) | ~16MB |
| Dozzle | 8888 | Real-time Docker log viewer | ~22MB |
| Socket Proxy | 2375 (localhost) | Read-only Docker API | ~15MB |

Total monitoring overhead: ~70MB RAM.

## Current Status

**M1: Protection Foundation** - Complete
- Dangerous cron fixed (removed `--volumes`)
- Database backups automated (every 6h, both Postgres containers)
- Backup verification with integrity checks
- All containers have restart policies (`unless-stopped`)
- Healthchecks documented for all containers
- Docker socket proxy deployed (read-only API on localhost:2375)

**M2: Monitoring Portal** - Complete
- Beszel hub + agent collecting system and Docker metrics
- Portainer CE showing all containers (read-only via socket-proxy)
- Dozzle streaming logs from all containers
- Alert thresholds documented

## Project Structure

```
qwickguard/
├── agent/                    # Local agent (runs on each server)
│   └── scripts/              # Backup and maintenance scripts
│       ├── backup.sh
│       ├── install-backup-cron.sh
│       └── verify-backups.sh
├── configs/
│   └── macmini-devserver.yaml
├── docker-compose.yml        # QwickGuard services
├── docs/
│   ├── plans/                # Design docs and implementation plans
│   └── runbooks/             # Operational runbooks
├── .env.example              # Environment variable template
└── README.md
```

## Quick Start

### Deploy monitoring stack

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d'
```

### Check backup status

```bash
ssh macmini-devserver '~/.qwickguard/scripts/verify-backups.sh'
```

### Check container health

```bash
ssh macmini-devserver 'docker ps --format "{{.Names}}: {{.Status}}"'
```

### View socket proxy (read-only Docker API)

```bash
ssh macmini-devserver 'curl -s http://127.0.0.1:2375/containers/json | python3 -m json.tool'
```

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| M1 | Protection Foundation | Complete |
| M2 | Monitoring Portal (Beszel, Portainer, Dozzle) | Complete |
| M3 | Local Agent (Python, Llama analysis) | Planned |
| M4 | Brain Service (FastAPI, Claude escalation) | Planned |
| M5 | Production Hardening | Planned |

## Links

- [Design Doc](docs/plans/2026-02-22-qwickguard-design.md)
- [M1 Implementation Plan](docs/plans/2026-02-22-m1-protection-foundation.md)
- [M2 Implementation Plan](docs/plans/2026-02-23-m2-monitoring-portal.md)
- [GitHub Project Board](https://github.com/users/raajkumars/projects/19)
