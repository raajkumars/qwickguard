# Runbook: Container Restart Policies

**Issue:** #17
**Date verified:** 2026-02-23
**Severity:** Low - all containers already compliant
**Time to verify:** ~2 minutes

---

## Overview

All Docker containers on macmini-devserver must use the `unless-stopped` restart policy. This ensures containers restart automatically after:

- Host reboots
- Docker daemon restarts
- Container crashes

Containers with `no` or `on-failure` restart policies will not survive host reboots, causing service outages.

---

## Current State (Verified 2026-02-23)

All 9 containers are compliant. Verified with:

```bash
for c in $(docker ps -aq); do
  docker inspect --format "{{.Name}}: restart={{.HostConfig.RestartPolicy.Name}}" $c
done
```

### Compose-Managed Containers

These containers are managed by `~/Projects/qwickbrain/docker-compose.yml`. Their restart policy is defined in the compose file and will be preserved across `docker compose up` calls.

| Container | Image | Restart Policy |
|-----------|-------|----------------|
| qwickbrain-node | qwickbrain-qwickbrain-node | unless-stopped |
| qwickbrain-server | qwickbrain-qwickbrain | unless-stopped |
| qwickbrain-postgres | postgres:16-alpine | unless-stopped |
| qwickbrain-redis | redis:7-alpine | unless-stopped |
| qwickbrain-qdrant | qdrant/qdrant:latest | unless-stopped |
| qwickbrain-neo4j | neo4j:5-community | unless-stopped |

### Standalone Containers

These containers are not managed by docker-compose. Their restart policy is set via `docker update` or at container creation via `--restart unless-stopped`.

| Container | Image | Restart Policy | Notes |
|-----------|-------|----------------|-------|
| faabzi-supertokens | registry.supertokens.io/supertokens/supertokens-postgresql:9.2 | unless-stopped | Standalone container |
| faabzi-postgres | postgres:15-alpine | unless-stopped | compose-managed (faabzi project) |
| trinity-university | trinity-university (local build) | unless-stopped | Standalone container, Next.js app on port 3700 |
Note: `faabzi-postgres` is managed by a separate faabzi docker-compose project (label: `com.docker.compose.project=faabzi`). Its restart policy is defined in that compose file.

---

## Verification Command

Run this to verify all containers have `unless-stopped`:

```bash
ssh macmini-devserver '
for c in $(docker ps -aq); do
  docker inspect --format "{{.Name}}: restart={{.HostConfig.RestartPolicy.Name}}" $c
done
'
```

Expected output: every line ends with `restart=unless-stopped`.

To check a single container:

```bash
ssh macmini-devserver 'docker inspect --format "{{.HostConfig.RestartPolicy.Name}}" <container_name>'
```

---

## Fixing a Container Without `unless-stopped`

### Compose-managed containers

Update the `restart:` field in the compose file, then re-deploy:

```yaml
services:
  my-service:
    restart: unless-stopped
```

```bash
docker compose up -d my-service
```

### Standalone containers

Use `docker update` on a running container (no downtime required):

```bash
docker update --restart unless-stopped <container_name>
```

Verify the fix:

```bash
docker inspect --format "{{.HostConfig.RestartPolicy.Name}}" <container_name>
```

---

## Adding New Containers

When deploying a new container on macmini-devserver:

**Via docker run:**

```bash
docker run -d --restart unless-stopped --name <name> <image>
```

**Via docker-compose:**

```yaml
services:
  my-service:
    restart: unless-stopped
```

**QwickGuard containers** (deployed via `~/Projects/qwickguard/docker-compose.yml`) must include `restart: unless-stopped` in their service definitions. The QwickGuard compose file enforces this for the socket-proxy service.

After adding a new container, run the verification command above to confirm compliance.

---

## Audit Frequency

This should be re-audited:

- After any new container is deployed
- After macmini-devserver is rebooted (to confirm containers came back up)
- During monthly infrastructure reviews
