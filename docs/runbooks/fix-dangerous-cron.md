# Runbook: Fix Dangerous Docker System Prune Cron

**Issue:** #14
**Date fixed:** 2026-02-22
**Severity:** Critical - data loss risk
**Time to fix:** ~5 minutes

---

## Problem

The macmini-devserver had a weekly cron job running every Sunday at 3 AM:

```
0 3 * * 0 /usr/local/bin/docker system prune -af --volumes 2>&1 | logger -t docker-cleanup
```

The `--volumes` flag deletes ALL unused Docker volumes. A volume becomes "unused" when its container is stopped (not running). If any container happens to be stopped at 3 AM Sunday - due to a restart, update, crash, or manual operation - its volume is permanently deleted.

The macmini runs 7 Docker containers with persistent volumes:

- PostgreSQL (faabzi database, work-macha database)
- Redis
- Neo4j
- Qdrant

Data loss from this cron is irreversible.

### What triggered this fix

A recent incident wiped a production database when a container was accidentally deleted. Audit of the macmini revealed this cron job posed the same risk on a weekly schedule.

---

## Fix Applied

Changed `--volumes` to `--filter "until=48h"` to clean only images/containers/networks older than 48 hours, without touching volumes at all.

**Before:**
```
0 3 * * 0 /usr/local/bin/docker system prune -af --volumes 2>&1 | logger -t docker-cleanup
```

**After:**
```
0 3 * * 0 /usr/local/bin/docker system prune -af --filter "until=48h" 2>&1 | logger -t docker-cleanup
```

### Why `--filter "until=48h"` instead of `--volumes`

- Cleans dangling images, stopped containers, and unused networks older than 48 hours
- Does NOT touch Docker volumes under any circumstances
- Reclaims disk space from stale build artifacts while protecting data
- Safe to run against live systems

---

## Steps to Re-Apply (if crontab is reset)

1. SSH to macmini-devserver:
   ```bash
   ssh macmini-devserver
   ```

2. Back up the current crontab:
   ```bash
   crontab -l > ~/crontab-backup-$(date +%Y%m%d).txt
   ```

3. Apply the fix:
   ```bash
   crontab -l | sed 's/docker system prune -af --volumes/docker system prune -af --filter "until=48h"/' | crontab -
   ```

4. Verify the fix:
   ```bash
   crontab -l
   ```
   Expected output contains `--filter "until=48h"` with no `--volumes` flag.

---

## Verification

After applying, confirm:

```bash
ssh macmini-devserver 'crontab -l'
```

Expected:
```
# Clean Docker weekly on Sunday at 3am
0 3 * * 0 /usr/local/bin/docker system prune -af --filter "until=48h" 2>&1 | logger -t docker-cleanup
```

Two checks:
1. `--volumes` is absent from the line
2. `--filter "until=48h"` is present

---

## What Was NOT Changed

- Schedule: still Sunday at 3 AM
- Logging: still pipes to `logger -t docker-cleanup` (visible via `log show --predicate 'senderImagePath contains "logger"'`)
- Scope: still cleans images, containers, and networks
- Only volumes are now protected

---

## Backup File

The original crontab was backed up on macmini-devserver at:
```
~/crontab-backup-20260222.txt
```

---

## Related

- Issue #14 (this fix)
- macmini-devserver Docker containers: faabzi-postgres, redis, neo4j, qdrant
- Volumes at risk before fix: PostgreSQL data, Redis AOF, Neo4j data, Qdrant storage
