# M2: Monitoring Portal - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy Beszel, Portainer CE, and Dozzle on macmini-devserver, all accessible via Tailscale, providing full monitoring visibility into system metrics, container state, and Docker logs.

**Architecture:** All monitoring services run as Docker containers orchestrated by docker-compose.yml. Portainer and Dozzle connect to the existing docker-socket-proxy (read-only API). Beszel agent runs with network_mode: host for accurate system metrics but communicates with the hub via a shared Unix socket (avoids macOS/Colima networking hairpin issues). Beszel agent mounts docker.sock directly (read-only) for container metrics.

**Tech Stack:** Docker Compose, Beszel (henrygd/beszel), Portainer CE (portainer/portainer-ce:lts), Dozzle (amir20/dozzle), tecnativa/docker-socket-proxy

**Related Issues:** #3, #5, #7, #8, #9, #12
**Design Doc:** `docs/plans/2026-02-22-qwickguard-design.md` (Section 7)

---

## Key Design Decisions

### Beszel Agent: Unix Socket, Not TCP

On macOS with Colima, `network_mode: host` binds to the VM's network, not the macOS host. This means port 45876 is not directly reachable from other containers. The fix: hub and agent share a Unix socket directory (`beszel_socket/`). In the Beszel hub UI, the host is set to `/beszel_socket/beszel.sock` instead of an IP:port.

### Beszel Agent: Direct docker.sock, Not Socket Proxy

The Beszel agent uses `network_mode: host`, which means it cannot resolve Docker bridge DNS names like `socket-proxy`. Using the socket proxy would require IP addresses, which are unstable. Simpler: mount `/var/run/docker.sock:ro` directly on the agent. The agent only reads container stats - it never writes.

### Portainer: Read-Only via Socket Proxy

Portainer connects to `tcp://socket-proxy:2375` via an internal Docker network. With `POST=0, DELETE=0`, Portainer works as a viewer only. Management actions (start/stop/restart) will show 403 errors. This is intentional - we want monitoring, not management.

### Dozzle: Read-Only via Socket Proxy

Dozzle connects to `tcp://socket-proxy:2375` via the same internal network. With `DOZZLE_ENABLE_ACTIONS=false` (default), it only uses GET requests. Fully functional as a log viewer.

### Network Architecture

```
┌─────────────────────────────────────────────────┐
│  qwickguard-internal (bridge, internal: true)   │
│                                                 │
│  socket-proxy ←── portainer (tcp://socket-proxy:2375)
│       ↑                                         │
│       └────────── dozzle    (tcp://socket-proxy:2375)
│                                                 │
│  beszel-hub (shares beszel_socket volume)       │
└─────────────────────────────────────────────────┘

  beszel-agent (network_mode: host)
    ├── /var/run/docker.sock:ro  (container metrics)
    └── beszel_socket volume     (communicates with hub)
```

---

## Task 1: Update docker-compose.yml with Internal Network (Issue #5, prep)

**10 minutes.**

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add internal network to socket-proxy**

Update `docker-compose.yml` to add:
1. A `qwickguard-internal` network (bridge, internal: true)
2. Attach socket-proxy to this network
3. Keep the existing `127.0.0.1:2375` port binding for host access

The network must be `internal: true` so monitoring containers cannot reach the internet through the socket-proxy network.

```yaml
services:
  socket-proxy:
    image: tecnativa/docker-socket-proxy
    container_name: qwickguard-socket-proxy
    restart: unless-stopped
    privileged: true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      CONTAINERS: 1
      SERVICES: 1
      TASKS: 1
      NETWORKS: 1
      VOLUMES: 1
      IMAGES: 1
      INFO: 1
      EVENTS: 1
      POST: 0
      DELETE: 0
    ports:
      - "127.0.0.1:2375:2375"
    networks:
      - qwickguard-internal
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:2375/_ping || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3

networks:
  qwickguard-internal:
    driver: bridge
    internal: true
```

**Step 2: Deploy and verify socket-proxy still works**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d socket-proxy'
ssh macmini-devserver 'curl -s http://127.0.0.1:2375/containers/json | python3 -c "import json,sys; print(f\"{len(json.load(sys.stdin))} containers visible\")"'
```

Expected: Container count > 0.

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add internal network for monitoring stack

Prep for Portainer and Dozzle to connect to socket-proxy
via internal bridge network."
```

---

## Task 2: Add Portainer CE Service (Issue #8)

**15 minutes.**

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add portainer service to docker-compose.yml**

```yaml
  portainer:
    image: portainer/portainer-ce:lts
    container_name: qwickguard-portainer
    restart: unless-stopped
    command:
      - -H
      - tcp://qwickguard-socket-proxy:2375
      - --tlsskipverify
    volumes:
      - portainer_data:/data
    ports:
      - "9000:9000"
    networks:
      - qwickguard-internal
    depends_on:
      socket-proxy:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:9000/api/status || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
```

Add to volumes section:

```yaml
volumes:
  portainer_data:
```

Note: The `-H` flag must be split into separate list items (Portainer bug #12037). Use the container name `qwickguard-socket-proxy` as the hostname since both are on the same `qwickguard-internal` network.

**Step 2: Deploy on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d portainer'
```

**Step 3: Verify Portainer is accessible**

```bash
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:9000/'
```

Expected: HTTP 200 (or 302 redirect to setup wizard).

**Step 4: Verify read operations work (containers visible)**

```bash
ssh macmini-devserver 'curl -sf http://localhost:9000/api/status | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"Version: {d.get(\"Version\", \"unknown\")}\")"'
```

**Step 5: Verify write operations are blocked**

After initial admin setup (done via browser), test that write operations fail:

```bash
# This should return 403 from socket-proxy
ssh macmini-devserver 'curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:2375/containers/faabzi-postgres/restart'
```

Expected: HTTP 403 or 405.

**Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Portainer CE (read-only via socket-proxy)

Connects to socket-proxy on internal network.
POST=0/DELETE=0 means view-only mode.
Closes #8"
```

---

## Task 3: Add Dozzle Service (Issue #9)

**10 minutes.**

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add dozzle service to docker-compose.yml**

```yaml
  dozzle:
    image: amir20/dozzle:latest
    container_name: qwickguard-dozzle
    restart: unless-stopped
    environment:
      DOZZLE_REMOTE_HOST: tcp://qwickguard-socket-proxy:2375
      DOZZLE_NO_ANALYTICS: "true"
    ports:
      - "8888:8080"
    networks:
      - qwickguard-internal
    depends_on:
      socket-proxy:
        condition: service_healthy
```

Note: Dozzle's default internal port is 8080. Map it to 8888 externally. No volume needed (stateless). `DOZZLE_ENABLE_ACTIONS` defaults to false (read-only).

**Step 2: Deploy on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d dozzle'
```

**Step 3: Verify Dozzle is accessible and shows containers**

```bash
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8888/'
```

Expected: HTTP 200.

**Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Dozzle log viewer (read-only via socket-proxy)

Real-time Docker log streaming on port 8888.
Closes #9"
```

---

## Task 4: Add Beszel Hub Service (Issue #7, part 1)

**15 minutes.**

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add beszel hub service to docker-compose.yml**

```yaml
  beszel:
    image: henrygd/beszel:latest
    container_name: qwickguard-beszel
    restart: unless-stopped
    ports:
      - "8090:8090"
    volumes:
      - beszel_data:/beszel_data
      - beszel_socket:/beszel_socket
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:8090/api/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

Add to volumes section:

```yaml
volumes:
  portainer_data:
  beszel_data:
  beszel_socket:
```

Note: Beszel hub does NOT need to be on the qwickguard-internal network. It communicates with the agent via the shared `beszel_socket` volume (Unix socket). It needs to be accessible on port 8090 from the host.

**Step 2: Deploy on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d beszel'
```

**Step 3: Verify Beszel hub is accessible**

```bash
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8090/'
```

Expected: HTTP 200.

**Step 4: Create admin account**

Open `http://macmini-devserver:8090` in browser. Create admin account on first access. This must be done interactively.

**Step 5: Get SSH public key for agent**

After logging in, go to "Add System" in the UI. Copy the SSH public key shown. This key will be used in the agent's `KEY` environment variable.

**Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Beszel hub for system monitoring

Web UI on port 8090. Uses shared Unix socket for agent communication.
Partial #7"
```

---

## Task 5: Add Beszel Agent Service (Issue #7, part 2)

**20 minutes. Requires the SSH key from Task 4 Step 5.**

**Files:**
- Modify: `docker-compose.yml`
- Create: `.env.example`

**Step 1: Add beszel-agent service to docker-compose.yml**

```yaml
  beszel-agent:
    image: henrygd/beszel-agent:latest
    container_name: qwickguard-beszel-agent
    restart: unless-stopped
    network_mode: host
    volumes:
      - beszel_socket:/beszel_socket
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      LISTEN: /beszel_socket/beszel.sock
      KEY: "${BESZEL_AGENT_KEY}"
```

Note: The agent uses `network_mode: host` for accurate system metrics. It communicates with the hub via the shared `beszel_socket` Unix socket volume. Docker container metrics come from the direct docker.sock mount (read-only).

**Step 2: Create .env.example**

```
# Beszel agent SSH public key (from Beszel hub UI > Add System)
BESZEL_AGENT_KEY=ssh-ed25519 AAAA...your-key-here
```

**Step 3: Create .env file on macmini with the actual key**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && echo "BESZEL_AGENT_KEY=ssh-ed25519 <PASTE_KEY_HERE>" > .env'
```

Replace `<PASTE_KEY_HERE>` with the actual key from Task 4 Step 5.

**Step 4: Deploy on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && docker compose up -d beszel-agent'
```

**Step 5: Configure hub to connect to agent**

In the Beszel hub UI (`http://macmini-devserver:8090`):
1. Click "Add System"
2. Set hostname to `macmini-devserver`
3. Set Host / IP to `/beszel_socket/beszel.sock`
4. Save

**Step 6: Verify agent is collecting metrics**

Wait 30 seconds, then check the Beszel hub UI. The system should show as online with CPU, RAM, disk, and Docker container metrics.

```bash
ssh macmini-devserver 'docker logs qwickguard-beszel-agent 2>&1 | tail -5'
```

Expected: No errors, agent listening on socket.

**Step 7: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add Beszel agent for system + Docker metrics

Agent uses Unix socket for hub communication (avoids macOS networking issues).
Mounts docker.sock directly for container metrics.
Closes #7"
```

---

## Task 6: Configure Beszel Alerts (Issue #12)

**15 minutes. Requires Beszel hub running with data.**

This task is done entirely through the Beszel web UI. No code changes.

**Step 1: Open Beszel hub**

Navigate to `http://macmini-devserver:8090` in browser.

**Step 2: Configure alert thresholds**

In Settings > Alerts, set:

| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | 80% for 5 min | 95% for 2 min |
| RAM | 85% | 95% |
| Disk | 80% | 90% |

**Step 3: Configure container alerts**

Set alerts for container health status changes (down/unhealthy) for:
- faabzi-postgres (critical)
- qwickbrain-server (critical)
- qwickbrain-node (critical)
- qwickbrain-postgres (critical)

**Step 4: Configure notification channel**

Beszel supports webhooks. If Slack/Discord webhook is available, configure it. Otherwise, use the default Beszel notification system (in-app alerts).

Document the alert configuration in a runbook.

**Step 5: Create alert documentation**

Create `docs/runbooks/beszel-alerts.md` documenting:
- Alert thresholds configured
- Notification channels
- How to add/modify alerts
- How to acknowledge alerts

**Step 6: Commit**

```bash
git add docs/runbooks/beszel-alerts.md
git commit -m "docs: document Beszel alert configuration

Closes #12"
```

---

## Task 7: Verify Full Stack and Update README

**15 minutes.**

**Step 1: Verify all services are running**

```bash
ssh macmini-devserver 'docker ps --filter "name=qwickguard-" --format "{{.Names}}: {{.Status}}"'
```

Expected: All 5 qwickguard containers healthy:
- qwickguard-socket-proxy (healthy)
- qwickguard-portainer (healthy)
- qwickguard-dozzle (Up)
- qwickguard-beszel (healthy)
- qwickguard-beszel-agent (Up)

**Step 2: Verify all UIs accessible via Tailscale**

```bash
# Beszel
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8090/'
# Portainer
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:9000/'
# Dozzle
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://localhost:8888/'
# Socket proxy
ssh macmini-devserver 'curl -sf -o /dev/null -w "%{http_code}" http://127.0.0.1:2375/_ping'
```

Expected: All return HTTP 200.

**Step 3: Check RAM overhead**

```bash
ssh macmini-devserver 'docker stats --no-stream --format "{{.Name}}: {{.MemUsage}}" $(docker ps -q --filter "name=qwickguard-")'
```

Expected: Total < 200MB across all qwickguard containers.

**Step 4: Update README.md**

Update the "Current Status" section to reflect M2 completion. Add monitoring portal URLs:

```
## Monitoring Portal

All accessible via Tailscale at `http://macmini-devserver:<port>`:

| Tool | Port | Purpose |
|------|------|---------|
| Beszel | 8090 | System metrics, Docker stats, alerts |
| Portainer | 9000 | Container management UI (read-only) |
| Dozzle | 8888 | Real-time Docker log viewer |
```

**Step 5: Close epic issue**

```bash
gh issue close 3 -c "M2 complete. All monitoring UIs deployed and accessible via Tailscale."
```

**Step 6: Commit and push**

```bash
git add README.md
git commit -m "docs: update README with M2 monitoring portal status"
git push
```

---

## Summary: M2 Task Execution Order

| # | Task | Issue | Est. | Depends On |
|---|------|-------|------|------------|
| 1 | Add internal network to docker-compose | #5 prep | 10 min | - |
| 2 | Add Portainer CE service | #8 | 15 min | Task 1 |
| 3 | Add Dozzle service | #9 | 10 min | Task 1 |
| 4 | Add Beszel hub service | #7 part 1 | 15 min | - |
| 5 | Add Beszel agent service | #7 part 2 | 20 min | Task 4 (need SSH key) |
| 6 | Configure Beszel alerts | #12 | 15 min | Task 5 |
| 7 | Verify full stack + update README | #3 | 15 min | All |

**Total: ~1.5 hours**

**Interactive steps requiring browser access:**
- Task 4 Step 4-5: Create Beszel admin account and get SSH key
- Task 5 Step 5: Configure hub-to-agent connection in Beszel UI
- Task 6 Steps 2-4: Configure alerts in Beszel UI

After M2, macmini-devserver has: full monitoring visibility via Beszel (metrics + alerts), Portainer (container viewer), and Dozzle (log streaming), all accessible via Tailscale. Total RAM overhead < 200MB.
