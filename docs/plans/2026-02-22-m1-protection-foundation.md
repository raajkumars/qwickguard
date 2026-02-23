# M1: Protection Foundation - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the top data loss risks on macmini-devserver: fix dangerous cron, set up automated database backups, harden Docker container policies, and deploy docker-socket-proxy.

**Architecture:** Shell scripts for backups (cron-based), Docker socket proxy container for monitoring tool isolation, direct SSH to macmini for cron/container fixes. No Python code in M1 - all infrastructure hardening.

**Tech Stack:** Bash, Docker, cron, pg_dump, macOS launchd

**Related Issues:** #1, #2, #4, #6, #11, #14, #17, #20, #23
**Design Doc:** `docs/plans/2026-02-22-qwickguard-design.md`

---

## Task 1: Fix Dangerous Docker System Prune Cron (Issue #14)

**CRITICAL - Do this first. 15 minutes. Prevents data loss.**

**Files:**
- Create: `docs/runbooks/fix-dangerous-cron.md`

**Step 1: Verify current crontab on macmini**

```bash
ssh macmini-devserver 'crontab -l'
```

Expected: See line containing `docker system prune -af --volumes`

**Step 2: Back up current crontab**

```bash
ssh macmini-devserver 'crontab -l > ~/crontab-backup-$(date +%Y%m%d).txt'
```

**Step 3: Replace dangerous cron entry**

```bash
ssh macmini-devserver 'crontab -l | sed "s/docker system prune -af --volumes/docker system prune -af --filter \"until=48h\"/" | crontab -'
```

**Step 4: Verify the fix**

```bash
ssh macmini-devserver 'crontab -l'
```

Expected: Line now shows `docker system prune -af --filter "until=48h"` with NO `--volumes` flag.

**Step 5: Document the change**

Create `docs/runbooks/fix-dangerous-cron.md` documenting what changed and why.

**Step 6: Commit**

```bash
git add docs/runbooks/fix-dangerous-cron.md
git commit -m "docs: document dangerous cron fix (issue #14)"
```

---

## Task 2: Create Backup Directory Structure (Issue #2, prep)

**Step 1: Create backup directories on macmini**

```bash
ssh macmini-devserver 'mkdir -p ~/.qwickguard/backups/faabzi-postgres ~/.qwickguard/backups/qwickbrain-postgres ~/.qwickguard/logs ~/.qwickguard/scripts ~/.qwickguard/flags'
```

**Step 2: Verify directories exist**

```bash
ssh macmini-devserver 'ls -la ~/.qwickguard/'
```

Expected: `backups/`, `logs/`, `scripts/`, `flags/` directories.

---

## Task 3: Write Backup Script (Issue #2)

**Files:**
- Create: `agent/scripts/backup.sh`

**Step 1: Write the backup script**

Create `agent/scripts/backup.sh` that:
1. Runs `docker exec faabzi-postgres pg_dumpall -U postgres | gzip` to dated file
2. Runs `docker exec qwickbrain-postgres pg_dumpall -U qwickbrain | gzip` to dated file
3. Verifies each dump file size > 1KB minimum
4. Rotates backups older than 14 days
5. Logs all operations to `~/.qwickguard/logs/backup.log`
6. Exits non-zero if any backup fails

Key implementation details:
- Check container is running before attempting backup (`docker inspect --format='{{.State.Running}}'`)
- Use `stat -f%z` (macOS) with `stat -c%s` fallback (Linux) for file size
- Use `find -mtime +14 -delete` for rotation
- Log format: `[YYYY-MM-DD HH:MM:SS] message`

**Step 2: Make executable and test on macmini**

```bash
chmod +x agent/scripts/backup.sh
scp agent/scripts/backup.sh macmini-devserver:~/.qwickguard/scripts/backup.sh
ssh macmini-devserver 'chmod +x ~/.qwickguard/scripts/backup.sh && ~/.qwickguard/scripts/backup.sh'
```

Expected: SUCCESS lines for both containers.

**Step 3: Verify backup files and integrity**

```bash
ssh macmini-devserver 'ls -lh ~/.qwickguard/backups/faabzi-postgres/ && ls -lh ~/.qwickguard/backups/qwickbrain-postgres/'
ssh macmini-devserver 'gunzip -t ~/.qwickguard/backups/faabzi-postgres/*.sql.gz && echo "faabzi OK" && gunzip -t ~/.qwickguard/backups/qwickbrain-postgres/*.sql.gz && echo "qwickbrain OK"'
```

**Step 4: Commit**

```bash
git add agent/scripts/backup.sh
git commit -m "feat: add pg_dump backup script for both Postgres containers

Closes #2"
```

---

## Task 4: Install Backup Cron Schedule (Issue #4)

**Files:**
- Create: `agent/scripts/install-backup-cron.sh`

**Step 1: Write the cron installer**

Create `agent/scripts/install-backup-cron.sh` that:
1. Checks backup script exists at `~/.qwickguard/scripts/backup.sh`
2. Removes existing QwickGuard cron entries (idempotent via marker comment)
3. Adds three cron entries:
   - `0 */6 * * *` - run backup every 6 hours
   - `0 3 * * 0` - delete logs older than 30 days (weekly)
   - `0 4 * * 0` - run verify-backups.sh (weekly)
4. Uses `# QwickGuard` marker for idempotent removal

**Step 2: Deploy and test**

```bash
chmod +x agent/scripts/install-backup-cron.sh
scp agent/scripts/install-backup-cron.sh macmini-devserver:~/.qwickguard/scripts/
ssh macmini-devserver '~/.qwickguard/scripts/install-backup-cron.sh'
ssh macmini-devserver 'crontab -l | grep QwickGuard'
```

Expected: Three cron entries printed.

**Step 3: Commit**

```bash
git add agent/scripts/install-backup-cron.sh
git commit -m "feat: add backup cron installer (every 6 hours + weekly verify)

Closes #4"
```

---

## Task 5: Add Backup Verification Script (Issue #6)

**Files:**
- Create: `agent/scripts/verify-backups.sh`

**Step 1: Write the verification script**

Create `agent/scripts/verify-backups.sh` that:
1. Checks recency: latest backup must be < 12 hours old
2. Checks gzip integrity: `gunzip -t` on latest backup
3. Checks SQL structure: first 20 lines contain PostgreSQL/pg_dumpall/CREATE markers
4. Sets flag files in `~/.qwickguard/flags/` for agent to detect:
   - `backup-missing-{container}` - no backups found
   - `backup-stale-{container}` - latest backup too old
   - `backup-corrupt-{container}` - integrity check failed
5. Clears flag files when checks pass
6. Runs for both faabzi-postgres and qwickbrain-postgres

**Step 2: Deploy and test**

```bash
chmod +x agent/scripts/verify-backups.sh
scp agent/scripts/verify-backups.sh macmini-devserver:~/.qwickguard/scripts/
ssh macmini-devserver '~/.qwickguard/scripts/verify-backups.sh'
```

Expected: OK lines for both containers.

**Step 3: Commit**

```bash
git add agent/scripts/verify-backups.sh
git commit -m "feat: add backup verification (integrity + recency checks)

Closes #6"
```

---

## Task 6: Verify and Enforce Restart Policies (Issue #17)

**Step 1: Audit all container restart policies**

```bash
ssh macmini-devserver 'for c in $(docker ps -aq); do docker inspect --format "{{.Name}}: restart={{.HostConfig.RestartPolicy.Name}}" $c; done'
```

**Step 2: Fix containers without unless-stopped**

```bash
ssh macmini-devserver 'docker update --restart unless-stopped faabzi-postgres faabzi-supertokens'
```

(Adjust based on Step 1 output.)

**Step 3: Verify and document**

```bash
ssh macmini-devserver 'for c in $(docker ps -aq); do docker inspect --format "{{.Name}}: restart={{.HostConfig.RestartPolicy.Name}}" $c; done'
```

Create `docs/runbooks/restart-policies.md` and commit.

```bash
git add docs/runbooks/restart-policies.md
git commit -m "docs: document container restart policies

Closes #17"
```

---

## Task 7: Add Healthchecks to Faabzi Stack (Issue #23)

**Step 1: Check existing healthchecks**

```bash
ssh macmini-devserver 'for c in faabzi-postgres faabzi-supertokens; do echo "=== $c ===" && docker inspect --format "Healthcheck: {{.Config.Healthcheck}}" $c; done'
```

**Step 2: Test healthcheck commands inside containers**

```bash
ssh macmini-devserver 'docker exec faabzi-postgres pg_isready -U postgres && echo "postgres OK"'
ssh macmini-devserver 'docker exec faabzi-supertokens curl -sf http://localhost:3567/hello >/dev/null 2>&1 && echo "supertokens OK" || docker exec faabzi-supertokens wget -q --spider http://localhost:3567/hello 2>/dev/null && echo "supertokens OK via wget"'
```

**Step 3: Document status and needed compose updates**

Create `docs/runbooks/healthchecks.md` with current status and required healthcheck configs.

```bash
git add docs/runbooks/healthchecks.md
git commit -m "docs: document container healthcheck status

Closes #23"
```

---

## Task 8: Deploy docker-socket-proxy (Issue #20)

**Files:**
- Create: `docker-compose.yml`

**Step 1: Write docker-compose.yml**

Create `docker-compose.yml` with socket-proxy service:
- Image: `tecnativa/docker-socket-proxy`
- Mount `/var/run/docker.sock:ro`
- Enable read operations: CONTAINERS, SERVICES, TASKS, NETWORKS, VOLUMES, IMAGES, INFO, EVENTS
- Disable write operations: POST=0, DELETE=0
- Bind to `127.0.0.1:2375` only
- Healthcheck: `wget -qO- http://localhost:2375/_ping`
- restart: unless-stopped

**Step 2: Ensure Colima socket symlink**

```bash
ssh macmini-devserver 'test -S /var/run/docker.sock && echo "socket exists" || sudo ln -sf $HOME/.colima/default/docker.sock /var/run/docker.sock'
```

**Step 3: Deploy on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d socket-proxy'
```

**Step 4: Verify read works, write blocked**

```bash
ssh macmini-devserver 'curl -s http://127.0.0.1:2375/containers/json | python3 -c "import json,sys; print(f\"{len(json.load(sys.stdin))} containers visible\")"'
ssh macmini-devserver 'curl -s -o /dev/null -w "%{http_code}" -X DELETE http://127.0.0.1:2375/containers/faabzi-postgres'
```

Expected: Container count > 0, HTTP 403/405 on delete.

**Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-compose with socket-proxy (read-only Docker API)

Closes #20"
```

---

## Task 9: Server Config + README + .gitignore

**Files:**
- Create: `configs/macmini-devserver.yaml` (from design doc Section 10)
- Create: `README.md` (project overview, quick start, dashboard URLs)
- Create: `.gitignore` (Python, env, IDE, backups, logs)

**Step 1: Create all three files**

See design doc for full content of each file.

**Step 2: Commit**

```bash
git add configs/macmini-devserver.yaml README.md .gitignore
git commit -m "feat: add server config, README, and .gitignore"
```

---

## Summary: M1 Task Execution Order

| # | Task | Issue | Est. | Priority |
|---|------|-------|------|----------|
| 1 | Fix dangerous cron | #14 | 15 min | CRITICAL |
| 2 | Create backup dirs | #2 prep | 5 min | High |
| 3 | Write backup script | #2 | 1 hr | High |
| 4 | Install backup cron | #4 | 30 min | High |
| 5 | Backup verification | #6 | 1 hr | High |
| 6 | Restart policies | #17 | 30 min | Medium |
| 7 | Healthchecks | #23 | 30 min | Medium |
| 8 | docker-socket-proxy | #20 | 1 hr | Medium |
| 9 | Config + README | - | 30 min | Low |

**Total: ~5.5 hours**

After M1, macmini-devserver has: automated backups (6h cycle, 14-day retention), no dangerous cron, restart policies on all containers, healthchecks documented, and docker-socket-proxy ready for M2 monitoring tools.
