# QwickGuard

AI-powered self-healing infrastructure agent for macmini-devserver (and future servers).

## What It Does

- **Automated backups** - pg_dump every 6 hours with 14-day retention and integrity verification
- **Container monitoring** - healthchecks, restart policies, and crash detection
- **Self-healing** - autonomous diagnosis via Llama, escalation to Claude API for complex issues
- **Monitoring portal** - Beszel (metrics), Portainer CE (containers), Dozzle (logs)
- **Notifications** - GitHub Issues (audit trail) + Slack/Discord (real-time alerts)

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

## Current Status

**M1: Protection Foundation** - Complete
- Dangerous cron fixed (removed `--volumes`)
- Database backups automated (every 6h, both Postgres containers)
- Backup verification with integrity checks
- All containers have restart policies (`unless-stopped`)
- Healthchecks documented for all containers
- Docker socket proxy deployed (read-only API on localhost:2375)

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
├── docker-compose.yml        # QwickGuard services (socket-proxy, etc.)
├── docs/
│   ├── plans/                # Design docs and implementation plans
│   └── runbooks/             # Operational runbooks
└── README.md
```

## Quick Start

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
| M2 | Monitoring Portal (Beszel, Portainer, Dozzle) | Planned |
| M3 | Local Agent (Python, Llama analysis) | Planned |
| M4 | Brain Service (FastAPI, Claude escalation) | Planned |
| M5 | Production Hardening | Planned |

## Links

- [Design Doc](docs/plans/2026-02-22-qwickguard-design.md)
- [M1 Implementation Plan](docs/plans/2026-02-22-m1-protection-foundation.md)
- [GitHub Project Board](https://github.com/users/raajkumars/projects/19)
