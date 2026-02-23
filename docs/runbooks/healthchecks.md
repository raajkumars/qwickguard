# Runbook: Container Healthchecks

**Issue:** #23
**Date verified:** 2026-02-23
**Severity:** Medium - faabzi-supertokens and trinity-university lack healthchecks

---

## Overview

Docker healthchecks allow the daemon to know whether a container is functioning correctly, not just running. Without a healthcheck, a container shows as "Up" even if the process inside has hung or crashed internally.

This runbook documents the healthcheck status of all containers on macmini-devserver and provides the required configuration for any containers missing healthchecks.

---

## Current State (Verified 2026-02-23)

Checked with:

```bash
for c in $(docker ps --format "{{.Names}}"); do
  echo "=== $c ===" && \
  docker inspect --format "{{if .Config.Healthcheck}}Healthcheck: {{.Config.Healthcheck.Test}}{{else}}NO HEALTHCHECK{{end}}" $c
done
```

### Containers WITH Healthchecks

| Container | Healthcheck Command | Interval | Timeout | Retries | Start Period |
|-----------|---------------------|----------|---------|---------|--------------|
| qwickbrain-node | `wget -qO- http://localhost:3600/api/health` | 30s | 10s | 3 | 90s |
| qwickbrain-server | `python -c "import httpx; httpx.get('http://localhost:8080/health', timeout=5).raise_for_status()"` | 30s | 10s | 3 | 60s |
| qwickbrain-postgres | `pg_isready -U qwickbrain` | 10s | 5s | 5 | - |
| qwickbrain-redis | `redis-cli ping` | 10s | 5s | 5 | - |
| qwickbrain-qdrant | `timeout 5 bash -c '</dev/tcp/localhost/6333'` | 10s | 5s | 5 | - |
| qwickbrain-neo4j | `wget -q --spider http://localhost:7474` | 10s | 5s | 10 | 30s |
| faabzi-postgres | `pg_isready -U postgres` | 10s | 5s | 5 | - |

All qwickbrain containers are compose-managed (`~/Projects/qwickbrain/docker-compose.yml`) — healthchecks are defined in that compose file.

`faabzi-postgres` healthcheck verified working:

```bash
docker exec faabzi-postgres pg_isready -U postgres
# Output: /var/run/postgresql:5432 - accepting connections
```

### Containers WITHOUT Healthchecks

| Container | Image | Type | Health Endpoint | Status |
|-----------|-------|------|-----------------|--------|
| faabzi-supertokens | registry.supertokens.io/supertokens/supertokens-postgresql:9.2 | Standalone | `curl -sf http://localhost:3567/hello` | NO HEALTHCHECK |
| trinity-university | trinity-university (local build) | Standalone | `node -e` HTTP check on `http://localhost:3000/api/health` | NO HEALTHCHECK |

`faabzi-supertokens` exposes its API on port 3567. The `/hello` endpoint returns HTTP 200 and body `Hello` when the service is healthy. `curl` is available inside the container (verified).

```bash
docker exec faabzi-supertokens curl -sf http://localhost:3567/hello
# Output: Hello
```

`trinity-university` is a Node.js app (Next.js) exposing port 3000 internally, mapped to port 3700 on host. The `/api/health` endpoint returns JSON `{"status":"ok","timestamp":"...","version":"unknown"}`. No `curl` or `wget` available inside the container, but `node` is available.

```bash
# From host (port 3700):
curl -sf http://localhost:3700/api/health
# Output: {"status":"ok","timestamp":"2026-02-23T07:11:48.099Z","version":"unknown"}

# From inside container (port 3000, using node):
docker exec trinity-university node -e "const http = require('http'); http.get('http://localhost:3000/api/health', (r) => { let d=''; r.on('data', c => d+=c); r.on('end', () => { process.exit(r.statusCode === 200 ? 0 : 1); }); }).on('error', () => process.exit(1));"
```

---

## Adding a Healthcheck to faabzi-supertokens

Healthchecks cannot be added to a running standalone container without recreating it. The container must be removed and re-created with the `--health-*` flags, or managed via docker-compose.

**Important:** Recreating `faabzi-supertokens` requires downtime. Schedule during a maintenance window.

### Option A: docker run (standalone)

```bash
docker stop faabzi-supertokens
docker rm faabzi-supertokens

docker run -d \
  --name faabzi-supertokens \
  --restart unless-stopped \
  -p 3567:3567 \
  -e POSTGRESQL_CONNECTION_URI="postgresql://postgres:postgres@host.docker.internal:5432/faabzi_dev" \
  --health-cmd="curl -sf http://localhost:3567/hello" \
  --health-interval=30s \
  --health-timeout=5s \
  --health-retries=3 \
  --health-start-period=10s \
  registry.supertokens.io/supertokens/supertokens-postgresql:9.2
```

### Option B: docker-compose (preferred for future management)

Add to a faabzi `docker-compose.yml`:

```yaml
services:
  faabzi-supertokens:
    image: registry.supertokens.io/supertokens/supertokens-postgresql:9.2
    container_name: faabzi-supertokens
    restart: unless-stopped
    ports:
      - "3567:3567"
    environment:
      POSTGRESQL_CONNECTION_URI: "postgresql://postgres:postgres@host.docker.internal:5432/faabzi_dev"
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:3567/hello"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

---

## Adding a Healthcheck to trinity-university

`trinity-university` is a standalone container with no `curl` or `wget`. The healthcheck must use `node` to make the HTTP request.

**Important:** Recreating `trinity-university` requires downtime. Schedule during a maintenance window.

### Capture Current Container Config First

Before recreating, capture the full container configuration:

```bash
docker inspect trinity-university > ~/trinity-university-backup.json
```

### Option A: docker run (standalone)

```bash
docker stop trinity-university
docker rm trinity-university

docker run -d \
  --name trinity-university \
  --restart unless-stopped \
  -p 3700:3000 \
  --health-cmd='node -e "const http = require(\"http\"); http.get(\"http://localhost:3000/api/health\", (r) => { process.exit(r.statusCode === 200 ? 0 : 1); }).on(\"error\", () => process.exit(1));"' \
  --health-interval=30s \
  --health-timeout=10s \
  --health-retries=3 \
  --health-start-period=30s \
  trinity-university
```

Note: The `--health-start-period=30s` gives Next.js time to start up before healthchecks begin.

### Option B: docker-compose (preferred for future management)

Add to a `docker-compose.yml`:

```yaml
services:
  trinity-university:
    image: trinity-university
    container_name: trinity-university
    restart: unless-stopped
    ports:
      - "3700:3000"
    healthcheck:
      test: ["CMD", "node", "-e", "const http = require('http'); http.get('http://localhost:3000/api/health', (r) => { process.exit(r.statusCode === 200 ? 0 : 1); }).on('error', () => process.exit(1));"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

### Verify After Recreation

```bash
# Wait ~35 seconds for start_period to pass
docker ps --filter name=trinity-university --format "{{.Names}}: {{.Status}}"
# Expected: trinity-university: Up N seconds (healthy)

# Test health endpoint still works
curl -sf http://localhost:3700/api/health
# Expected: {"status":"ok",...}
```

---

## QwickGuard Containers

QwickGuard containers (defined in `~/Projects/qwickguard/docker-compose.yml`) must include healthchecks. The `qwickguard-socket-proxy` service includes a healthcheck using `wget`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- http://localhost:2375/_ping || exit 1"]
  interval: 30s
  timeout: 5s
  retries: 3
```

---

## Verification Command

Check all containers' healthcheck status:

```bash
ssh macmini-devserver '
for c in $(docker ps --format "{{.Names}}"); do
  echo "=== $c ===" && \
  docker inspect --format "{{if .Config.Healthcheck}}{{.Config.Healthcheck.Test}}{{else}}NO HEALTHCHECK{{end}}" $c
done
'
```

Check current health state of a specific container:

```bash
ssh macmini-devserver 'docker inspect --format "{{.State.Health.Status}}" <container_name>'
```

List all containers with their health state:

```bash
ssh macmini-devserver 'docker ps --format "{{.Names}}: {{.Status}}"'
```

---

## Audit Frequency

Re-audit after:

- Any new container is deployed (must include healthcheck)
- `faabzi-supertokens` or `trinity-university` is recreated (verify healthcheck takes effect)
- Docker or Colima version upgrades
