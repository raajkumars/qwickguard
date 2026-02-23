# M3: Local Agent - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the QwickGuard local agent - a Python process that collects system/Docker/service metrics, analyzes them via Llama (compute worker), takes autonomous healing actions, and reports to the brain.

**Architecture:** Python 3.11+ package running as macOS LaunchAgent. 5-minute loop: collect → analyze → heal → report. Uses psutil for system metrics, Docker SDK for containers, httpx for HTTP checks and compute worker calls. Action catalog with cooldowns prevents action storms. Audit log for all actions.

**Tech Stack:** Python 3.11+, psutil, docker (Docker SDK), httpx, pydantic, pydantic-settings, PyYAML, asyncio

**Related Issues:** #10 (epic), #13, #16, #19, #22, #25, #27, #29
**Design Doc:** `docs/plans/2026-02-22-qwickguard-design.md` (Sections 4, 8, 9, 10)

---

## Context: Compute Worker API

The existing qwickai compute worker runs on macmini at port 8001.

**Inference endpoint:** `POST http://localhost:8001/api/infer`

```json
{
  "model": "llama3.2:3b",
  "type": "completion",
  "input": {
    "prompt": "...",
    "maxTokens": 512,
    "temperature": 0.1
  }
}
```

**Response:**
```json
{
  "text": "...",
  "tokens": 123,
  "model": "llama3.2:3b",
  "message": {"role": "assistant", "content": "..."}
}
```

**Health:** `GET http://localhost:8001/health` returns worker status, loaded models, system load.

**Available models:** llama3.2:3b (hot), llama-3.1-8b (warm), qwen-2.5-14b (warm)

---

## Task 1: Scaffold Python Package (Issue #13)

**Files:**
- Create: `agent/pyproject.toml`
- Create: `agent/src/qwickguard_agent/__init__.py`
- Create: `agent/src/qwickguard_agent/main.py`
- Create: `agent/src/qwickguard_agent/config.py`
- Create: `agent/src/qwickguard_agent/models.py`
- Create: `agent/src/qwickguard_agent/collectors/__init__.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "qwickguard-agent"
version = "0.1.0"
description = "QwickGuard local monitoring and healing agent"
requires-python = ">=3.11"
dependencies = [
    "psutil>=5.9",
    "docker>=7.0",
    "httpx>=0.27",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-mock>=3.12"]

[project.scripts]
qwickguard-agent = "qwickguard_agent.main:main"
```

**Step 2: Create models.py with Pydantic models**

Define all data models used across the agent:
- `SystemMetrics`: cpu_percent, ram_percent, disk_percent, load_avg, open_files, uptime_seconds
- `ContainerStatus`: name, status, health, restart_count, cpu_percent, memory_mb, uptime_seconds
- `ServiceHealth`: name, url, healthy, response_time_ms, error
- `ProcessInfo`: name, pid, pattern, alive
- `CollectedMetrics`: system, containers, services, processes, timestamp
- `AnalysisResult`: status (healthy/warning/critical), issues list, actions list, escalate_to_claude bool
- `ActionResult`: action, target, reason, decided_by, result (success/failed/skipped/cooldown), error
- `AgentReport`: agent_id, hostname, timestamp, metrics, analysis, actions_taken

**Step 3: Create config.py**

Load server config from `configs/macmini-devserver.yaml`:
- Use pydantic-settings for environment variable overrides
- `QWICKGUARD_CONFIG` env var for config file path (default: `/Users/raajkumars/Projects/qwickguard/configs/macmini-devserver.yaml`)
- Parse YAML into typed Pydantic models (ServerConfig with nested ThresholdConfig, ContainerConfig, ServiceConfig, BackupConfig)

**Step 4: Create main.py entry point**

```python
import asyncio
import logging
from datetime import datetime

from .config import load_config

logger = logging.getLogger("qwickguard")

async def run_cycle(config):
    """Single monitoring cycle: collect → analyze → heal → report."""
    logger.info("Starting monitoring cycle")
    # Placeholder - each step added in subsequent tasks
    logger.info("Cycle complete")

async def agent_loop(config):
    """Main loop running every check_interval_seconds."""
    while True:
        try:
            await run_cycle(config)
        except Exception:
            logger.exception("Cycle failed")
        await asyncio.sleep(config.check_interval_seconds)

def main():
    config = load_config()
    setup_logging(config)
    logger.info(f"QwickGuard agent starting for {config.hostname}")
    asyncio.run(agent_loop(config))
```

**Step 5: Create empty collectors/__init__.py**

**Step 6: Verify install and entry point**

```bash
cd agent && pip install -e . && python -m qwickguard_agent.main --help 2>&1 || qwickguard-agent --help
```

Expected: Agent starts, logs "Starting monitoring cycle", runs one cycle, sleeps.

**Step 7: Commit**

```bash
git add agent/
git commit -m "feat: scaffold QwickGuard agent Python package

Closes #13"
```

---

## Task 2: System Metric Collectors (Issue #16)

**Files:**
- Create: `agent/src/qwickguard_agent/collectors/system.py`
- Create: `agent/tests/test_collectors_system.py`

**Step 1: Write system.py**

Collect via psutil:
- `cpu_percent`: `psutil.cpu_percent(interval=1)`
- `ram_percent`: `psutil.virtual_memory().percent`
- `ram_available_gb`: `psutil.virtual_memory().available / (1024**3)`
- `disk_percent`: `psutil.disk_usage('/').percent`
- `disk_available_gb`: `psutil.disk_usage('/').free / (1024**3)`
- `load_avg`: `psutil.getloadavg()` (tuple of 1m, 5m, 15m)
- `open_files`: `len(psutil.Process().open_files())` with try/except
- `uptime_seconds`: `time.time() - psutil.boot_time()`
- `temperature`: Try `psutil.sensors_temperatures()`, return None if unavailable (macOS often lacks this)

Return `SystemMetrics` Pydantic model.

Function: `async def collect_system_metrics() -> SystemMetrics`

**Step 2: Write tests**

```python
def test_collect_system_metrics():
    metrics = asyncio.run(collect_system_metrics())
    assert 0 <= metrics.cpu_percent <= 100
    assert 0 <= metrics.ram_percent <= 100
    assert 0 <= metrics.disk_percent <= 100
    assert len(metrics.load_avg) == 3
    assert metrics.uptime_seconds > 0
```

**Step 3: Run tests**

```bash
cd agent && pip install -e ".[dev]" && pytest tests/test_collectors_system.py -v
```

**Step 4: Commit**

```bash
git add agent/src/qwickguard_agent/collectors/system.py agent/tests/test_collectors_system.py
git commit -m "feat: add system metric collectors (CPU, RAM, disk, load)

Closes #16"
```

---

## Task 3: Docker Container Collectors (Issue #19)

**Files:**
- Create: `agent/src/qwickguard_agent/collectors/docker.py`
- Create: `agent/tests/test_collectors_docker.py`

**Step 1: Write docker.py**

Use Docker SDK (`docker` package):

```python
import docker

async def collect_docker_metrics(config) -> list[ContainerStatus]:
    client = docker.from_env()
    results = []
    for container in client.containers.list(all=True):
        stats = container.stats(stream=False)
        health = container.attrs.get("State", {}).get("Health", {}).get("Status", "none")
        restart_count = container.attrs.get("RestartCount", 0)
        # Calculate CPU % from stats
        # Calculate memory MB from stats
        results.append(ContainerStatus(
            name=container.name,
            status=container.status,  # running, exited, etc.
            health=health,  # healthy, unhealthy, none
            restart_count=restart_count,
            cpu_percent=cpu_pct,
            memory_mb=mem_mb,
            uptime_seconds=uptime,
        ))
    # Detect missing containers (in config but not found)
    configured_names = {c.name for c in config.containers}
    running_names = {c.name for c in results}
    for name in configured_names - running_names:
        results.append(ContainerStatus(
            name=name, status="missing", health="none",
            restart_count=0, cpu_percent=0, memory_mb=0, uptime_seconds=0,
        ))
    return results
```

Key details:
- CPU calculation: `(delta_container_cpu / delta_system_cpu) * num_cpus * 100`
- Memory: `stats["memory_stats"]["usage"] / (1024 * 1024)`
- Handle containers with no stats (exited/created) gracefully
- Close Docker client after use

**Step 2: Write tests**

Test with mock Docker client. Also write one integration test that actually connects to Docker (skip if Docker unavailable):

```python
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_collect_docker_metrics_integration():
    metrics = asyncio.run(collect_docker_metrics(config))
    assert len(metrics) > 0
    for m in metrics:
        assert m.name
        assert m.status in ("running", "exited", "created", "missing", "paused")
```

**Step 3: Run tests**

```bash
cd agent && pytest tests/test_collectors_docker.py -v
```

**Step 4: Commit**

```bash
git add agent/src/qwickguard_agent/collectors/docker.py agent/tests/test_collectors_docker.py
git commit -m "feat: add Docker container collectors (status, health, resources)

Closes #19"
```

---

## Task 4: Service Health and Process Collectors (Issue #22)

**Files:**
- Create: `agent/src/qwickguard_agent/collectors/services.py`
- Create: `agent/src/qwickguard_agent/collectors/processes.py`
- Create: `agent/tests/test_collectors_services.py`
- Create: `agent/tests/test_collectors_processes.py`

**Step 1: Write services.py**

HTTP health checks for configured endpoints:

```python
import httpx
import time

async def collect_service_health(config) -> list[ServiceHealth]:
    results = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for svc in config.services:
            start = time.monotonic()
            try:
                resp = await client.get(svc.url)
                elapsed = (time.monotonic() - start) * 1000
                results.append(ServiceHealth(
                    name=svc.name, url=svc.url,
                    healthy=resp.status_code == 200,
                    response_time_ms=round(elapsed, 1),
                    error=None if resp.status_code == 200 else f"HTTP {resp.status_code}",
                ))
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000
                results.append(ServiceHealth(
                    name=svc.name, url=svc.url,
                    healthy=False, response_time_ms=round(elapsed, 1),
                    error=str(e),
                ))
    return results
```

**Step 2: Write processes.py**

Detect GitHub runners and zombies:

```python
import psutil

async def collect_process_info(config) -> list[ProcessInfo]:
    results = []
    # Check GitHub runners
    for runner_path in config.github_runners:
        alive = any(
            runner_path in " ".join(p.cmdline())
            for p in psutil.process_iter(["cmdline"])
            if p.info["cmdline"]
        )
        results.append(ProcessInfo(
            name=f"runner:{runner_path}", pid=None,
            pattern=runner_path, alive=alive,
        ))
    # Detect zombies by pattern
    zombies = []
    for pattern in config.zombie_patterns:
        for proc in psutil.process_iter(["pid", "cmdline", "status"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if pattern in cmdline:
                    zombies.append(ProcessInfo(
                        name=f"zombie:{pattern}", pid=proc.info["pid"],
                        pattern=pattern, alive=True,
                    ))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    results.extend(zombies)
    return results
```

**Step 3: Write tests for both modules**

Mock httpx for services tests. Mock psutil for process tests.

**Step 4: Run tests**

```bash
cd agent && pytest tests/test_collectors_services.py tests/test_collectors_processes.py -v
```

**Step 5: Commit**

```bash
git add agent/src/qwickguard_agent/collectors/services.py agent/src/qwickguard_agent/collectors/processes.py agent/tests/
git commit -m "feat: add service health and process collectors

Closes #22"
```

---

## Task 5: Compute Worker Integration - Llama Analyzer (Issue #25)

**Files:**
- Create: `agent/src/qwickguard_agent/analyzer.py`
- Create: `agent/src/qwickguard_agent/prompts.py`
- Create: `agent/tests/test_analyzer.py`

**Step 1: Write prompts.py**

Build analysis prompts for Llama:

```python
def build_analysis_prompt(metrics: CollectedMetrics, config: ServerConfig) -> str:
    """Build a structured prompt for Llama analysis."""
    return f"""You are QwickGuard, an infrastructure monitoring agent for {config.hostname}.

Analyze these metrics and respond with JSON only.

## System Metrics
- CPU: {metrics.system.cpu_percent}% (warning: {config.thresholds.cpu_warning}%, critical: {config.thresholds.cpu_critical}%)
- RAM: {metrics.system.ram_percent}% (warning: {config.thresholds.ram_warning}%, critical: {config.thresholds.ram_critical}%)
- Disk: {metrics.system.disk_percent}% (warning: {config.thresholds.disk_warning}%, critical: {config.thresholds.disk_critical}%)
- Load: {metrics.system.load_avg}
- Open files: {metrics.system.open_files}

## Container Status
{format_containers(metrics.containers)}

## Service Health
{format_services(metrics.services)}

## Zombie Processes
{format_zombies(metrics.processes)}

Respond with this JSON structure:
{{
  "status": "healthy" | "warning" | "critical",
  "issues": ["description of each issue"],
  "actions": [
    {{"action": "action_name", "target": "target_name", "reason": "why"}}
  ],
  "escalate_to_claude": false
}}

Rules:
- Only recommend actions from: restart_container, docker_compose_up, kill_zombies, prune_images, run_backup, restart_colima, rotate_logs
- Set escalate_to_claude=true if multiple critical issues or unclear root cause
- Be conservative: only recommend actions when clearly needed"""
```

**Step 2: Write analyzer.py**

```python
import httpx
import json
import logging

logger = logging.getLogger("qwickguard.analyzer")

SEVERITY_MODELS = {
    "routine": "llama3.2:3b",
    "warning": "llama-3.1-8b",
    "critical": "qwen-2.5-14b",
}

async def analyze_metrics(metrics, config) -> AnalysisResult:
    """Analyze metrics via compute worker. Falls back to thresholds if unavailable."""
    try:
        return await _llama_analysis(metrics, config)
    except Exception as e:
        logger.warning(f"Compute worker unavailable: {e}. Using threshold fallback.")
        return _threshold_fallback(metrics, config)

async def _llama_analysis(metrics, config) -> AnalysisResult:
    # Determine severity for model selection
    severity = _determine_severity(metrics, config)
    model = SEVERITY_MODELS.get(severity, "llama3.2:3b")
    prompt = build_analysis_prompt(metrics, config)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{config.compute_worker_url}/api/infer", json={
            "model": model,
            "type": "completion",
            "input": {
                "prompt": prompt,
                "maxTokens": 512,
                "temperature": 0.1,
            }
        })
        resp.raise_for_status()
        data = resp.json()

    # Parse JSON from response text
    text = data.get("text") or data.get("message", {}).get("content", "")
    return _parse_analysis(text)

def _threshold_fallback(metrics, config) -> AnalysisResult:
    """Simple threshold-based analysis when Llama is unavailable."""
    issues = []
    actions = []
    status = "healthy"

    # Check system thresholds
    if metrics.system.cpu_percent >= config.thresholds.cpu_critical:
        issues.append(f"CPU critical: {metrics.system.cpu_percent}%")
        status = "critical"
    elif metrics.system.cpu_percent >= config.thresholds.cpu_warning:
        issues.append(f"CPU warning: {metrics.system.cpu_percent}%")
        status = "warning"

    # RAM, disk checks similar...

    # Check containers
    for c in metrics.containers:
        if c.status == "missing":
            issues.append(f"Container {c.name} is missing")
            # Find compose file from config
            cfg = next((x for x in config.containers if x.name == c.name), None)
            if cfg and cfg.compose_file:
                actions.append({"action": "docker_compose_up", "target": cfg.compose_file, "reason": f"{c.name} missing"})
            else:
                actions.append({"action": "restart_container", "target": c.name, "reason": f"{c.name} missing"})
            status = "critical" if cfg and cfg.critical else "warning"
        elif c.health == "unhealthy":
            issues.append(f"Container {c.name} unhealthy")
            actions.append({"action": "restart_container", "target": c.name, "reason": "unhealthy"})
            status = max(status, "warning", key=["healthy", "warning", "critical"].index)

    # Check zombies
    zombies = [p for p in metrics.processes if p.name.startswith("zombie:")]
    if zombies:
        patterns = set(p.pattern for p in zombies)
        for pattern in patterns:
            issues.append(f"Zombie processes matching '{pattern}'")
            actions.append({"action": "kill_zombies", "target": pattern, "reason": "zombie detected"})

    return AnalysisResult(
        status=status, issues=issues, actions=actions,
        escalate_to_claude=len([i for i in issues if "critical" in i.lower()]) > 2,
    )
```

**Step 3: Write tests**

Test both Llama path (mock httpx) and threshold fallback with various metric scenarios.

**Step 4: Run tests**

```bash
cd agent && pytest tests/test_analyzer.py -v
```

**Step 5: Commit**

```bash
git add agent/src/qwickguard_agent/analyzer.py agent/src/qwickguard_agent/prompts.py agent/tests/test_analyzer.py
git commit -m "feat: add Llama analyzer with compute worker integration

Tiered models: llama3.2:3b (routine), llama-3.1-8b (warning), qwen-2.5-14b (critical).
Threshold fallback when compute worker unavailable.

Closes #25"
```

---

## Task 6: Autonomous Healer (Issue #27)

**Files:**
- Create: `agent/src/qwickguard_agent/healer.py`
- Create: `agent/tests/test_healer.py`

**Step 1: Write healer.py**

```python
import asyncio
import json
import logging
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("qwickguard.healer")

ACTION_CATALOG = {
    "restart_container":  "docker restart {target}",
    "docker_compose_up":  "docker compose -f {target} up -d",
    "kill_zombies":       "pkill -9 -f '{target}'",
    "prune_images":       "docker system prune -af",
    "run_backup":         "{home}/.qwickguard/scripts/backup.sh",
    "restart_colima":     "colima restart",
    "rotate_logs":        "find {target} -name '*.log' -mtime +7 -delete",
}

COOLDOWNS = {
    "restart_container": {"max_count": 3, "window_seconds": 1800},  # 3 per 30 min per target
    "prune_images":      {"max_count": 1, "window_seconds": 86400}, # 1 per day
    "restart_colima":    {"max_count": 1, "window_seconds": 3600},  # 1 per hour
    "kill_zombies":      {"max_count": 5, "window_seconds": 3600},  # 5 per hour
    "run_backup":        {"max_count": 1, "window_seconds": 3600},  # 1 per hour
}

# NEVER ALLOWED - explicitly rejected
BLOCKED_PATTERNS = ["docker rm", "docker volume rm", "docker system prune --volumes",
                    "DROP ", "DELETE FROM", "TRUNCATE", "git push", "git checkout"]

class CooldownTracker:
    def __init__(self):
        self._history: dict[str, list[float]] = defaultdict(list)

    def check(self, action: str, target: str) -> bool:
        """Return True if action is allowed (not in cooldown)."""
        key = f"{action}:{target}"
        cooldown = COOLDOWNS.get(action)
        if not cooldown:
            return True
        now = time.time()
        window = cooldown["window_seconds"]
        # Prune old entries
        self._history[key] = [t for t in self._history[key] if now - t < window]
        return len(self._history[key]) < cooldown["max_count"]

    def record(self, action: str, target: str):
        key = f"{action}:{target}"
        self._history[key].append(time.time())

class Healer:
    def __init__(self, config, audit_log_path: Path):
        self.config = config
        self.cooldowns = CooldownTracker()
        self.audit_log_path = audit_log_path

    async def execute_actions(self, actions: list[dict], decided_by: str) -> list[ActionResult]:
        results = []
        for action_spec in actions:
            action = action_spec["action"]
            target = action_spec.get("target", "")
            reason = action_spec.get("reason", "")

            # Validate action in catalog
            if action not in ACTION_CATALOG:
                result = ActionResult(action=action, target=target, reason=reason,
                                      decided_by=decided_by, result="rejected",
                                      error=f"Action '{action}' not in catalog")
                self._audit(result)
                results.append(result)
                continue

            # Validate no blocked patterns
            cmd = self._build_command(action, target)
            if any(blocked in cmd for blocked in BLOCKED_PATTERNS):
                result = ActionResult(action=action, target=target, reason=reason,
                                      decided_by=decided_by, result="rejected",
                                      error=f"Command contains blocked pattern")
                self._audit(result)
                results.append(result)
                continue

            # Check cooldown
            if not self.cooldowns.check(action, target):
                result = ActionResult(action=action, target=target, reason=reason,
                                      decided_by=decided_by, result="cooldown",
                                      error=f"Cooldown active for {action}:{target}")
                self._audit(result)
                results.append(result)
                continue

            # Validate target against config (container names, etc.)
            if action == "restart_container":
                known = {c.name for c in self.config.containers}
                if target not in known:
                    result = ActionResult(action=action, target=target, reason=reason,
                                          decided_by=decided_by, result="rejected",
                                          error=f"Unknown container: {target}")
                    self._audit(result)
                    results.append(result)
                    continue

            # Execute
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                success = proc.returncode == 0
                self.cooldowns.record(action, target)
                result = ActionResult(
                    action=action, target=target, reason=reason,
                    decided_by=decided_by,
                    result="success" if success else "failed",
                    error=stderr.decode().strip() if not success else None,
                )
            except asyncio.TimeoutError:
                result = ActionResult(action=action, target=target, reason=reason,
                                      decided_by=decided_by, result="failed",
                                      error="Command timed out after 60s")
            except Exception as e:
                result = ActionResult(action=action, target=target, reason=reason,
                                      decided_by=decided_by, result="failed", error=str(e))

            self._audit(result)
            results.append(result)
        return results

    def _build_command(self, action: str, target: str) -> str:
        template = ACTION_CATALOG[action]
        home = str(Path.home())
        return template.format(target=target, home=home)

    def _audit(self, result: ActionResult):
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": result.action,
            "target": result.target,
            "reason": result.reason,
            "decided_by": result.decided_by,
            "result": result.result,
            "error": result.error,
        }
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.audit_log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"ACTION: {result.action} {result.target} -> {result.result}")
```

**Step 2: Write tests**

Test all paths:
- Action in catalog executes successfully (mock subprocess)
- Action NOT in catalog is rejected
- Blocked pattern is rejected
- Cooldown prevents execution
- Unknown container target is rejected
- Audit log is written for every action
- Timeout handling

**Step 3: Run tests**

```bash
cd agent && pytest tests/test_healer.py -v
```

**Step 4: Commit**

```bash
git add agent/src/qwickguard_agent/healer.py agent/tests/test_healer.py
git commit -m "feat: add autonomous healer with action catalog and cooldowns

7 allowed actions, cooldown enforcement, blocked pattern validation,
container name validation, audit logging for every action.

Closes #27"
```

---

## Task 7: Wire Up Core Loop and Reporter (Issues #10, #13)

**Files:**
- Create: `agent/src/qwickguard_agent/reporter.py`
- Modify: `agent/src/qwickguard_agent/main.py`
- Create: `agent/tests/test_main.py`

**Step 1: Write reporter.py**

```python
import httpx
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("qwickguard.reporter")

QUEUE_PATH = Path.home() / ".qwickguard" / "report_queue"

async def report_to_brain(report: AgentReport, brain_url: str):
    """Send report to brain. Queue locally if brain unreachable."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{brain_url}/api/v1/agents/{report.agent_id}/report",
                json=report.model_dump(mode="json"),
            )
            resp.raise_for_status()
            logger.info(f"Report sent to brain: {resp.status_code}")
            # Replay any queued reports
            await _replay_queue(brain_url)
    except Exception as e:
        logger.warning(f"Brain unreachable: {e}. Queuing report locally.")
        _queue_report(report)

def _queue_report(report: AgentReport):
    QUEUE_PATH.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    (QUEUE_PATH / filename).write_text(
        json.dumps(report.model_dump(mode="json"))
    )

async def _replay_queue(brain_url: str):
    if not QUEUE_PATH.exists():
        return
    for f in sorted(QUEUE_PATH.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{brain_url}/api/v1/agents/{data['agent_id']}/report",
                    json=data,
                )
            f.unlink()
            logger.info(f"Replayed queued report: {f.name}")
        except Exception:
            break  # Stop replay on first failure
```

**Step 2: Update main.py with full cycle**

Wire up the complete collect → analyze → heal → report cycle:

```python
async def run_cycle(config, healer):
    """Single monitoring cycle."""
    # 1. COLLECT
    system = await collect_system_metrics()
    containers = await collect_docker_metrics(config)
    services = await collect_service_health(config)
    processes = await collect_process_info(config)

    metrics = CollectedMetrics(
        system=system, containers=containers,
        services=services, processes=processes,
        timestamp=datetime.utcnow(),
    )

    # 2. ANALYZE
    analysis = await analyze_metrics(metrics, config)
    logger.info(f"Analysis: status={analysis.status}, issues={len(analysis.issues)}, actions={len(analysis.actions)}")

    # 3. ACT
    actions_taken = []
    if analysis.actions:
        decided_by = "llama" if config.compute_worker_url else "threshold"
        actions_taken = await healer.execute_actions(analysis.actions, decided_by)

    # 4. REPORT
    report = AgentReport(
        agent_id=config.agent_id,
        hostname=config.hostname,
        timestamp=datetime.utcnow(),
        metrics=metrics,
        analysis=analysis,
        actions_taken=actions_taken,
    )
    await report_to_brain(report, config.brain_url)
```

**Step 3: Write integration test for main cycle**

Mock all external dependencies (Docker, httpx, psutil) and verify the cycle completes.

**Step 4: Run tests**

```bash
cd agent && pytest tests/ -v
```

**Step 5: Commit**

```bash
git add agent/src/qwickguard_agent/reporter.py agent/src/qwickguard_agent/main.py agent/tests/test_main.py
git commit -m "feat: wire up core agent loop (collect → analyze → heal → report)

Complete monitoring cycle with local report queuing when brain unreachable."
```

---

## Task 8: LaunchAgent and Install Script (Issue #29)

**Files:**
- Create: `agent/templates/com.qwickapps.qwickguard-agent.plist`
- Create: `agent/scripts/install-agent.sh`
- Create: `agent/scripts/uninstall-agent.sh`

**Step 1: Create LaunchAgent plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.qwickapps.qwickguard-agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>__VENV_PATH__/bin/qwickguard-agent</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>QWICKGUARD_CONFIG</key>
        <string>__CONFIG_PATH__</string>
    </dict>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>__HOME__/.qwickguard/logs/agent.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>__HOME__/.qwickguard/logs/agent.stderr.log</string>
    <key>ThrottleInterval</key>
    <integer>30</integer>
</dict>
</plist>
```

**Step 2: Create install-agent.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
QG_HOME="$HOME/.qwickguard"
VENV_PATH="$QG_HOME/venv"
PLIST_NAME="com.qwickapps.qwickguard-agent"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=== QwickGuard Agent Installer ==="

# 1. Create directory structure
echo "[1/6] Creating directories..."
mkdir -p "$QG_HOME"/{logs,backups/faabzi-postgres,backups/qwickbrain-postgres,scripts,flags,report_queue}

# 2. Create Python venv and install
echo "[2/6] Setting up Python environment..."
python3 -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip
"$VENV_PATH/bin/pip" install -e "$AGENT_DIR"

# 3. Copy scripts
echo "[3/6] Installing scripts..."
cp "$AGENT_DIR/scripts/backup.sh" "$QG_HOME/scripts/"
cp "$AGENT_DIR/scripts/verify-backups.sh" "$QG_HOME/scripts/"
cp "$AGENT_DIR/scripts/install-backup-cron.sh" "$QG_HOME/scripts/"
chmod +x "$QG_HOME/scripts/"*.sh

# 4. Install LaunchAgent
echo "[4/6] Installing LaunchAgent..."
CONFIG_PATH="${QWICKGUARD_CONFIG:-$AGENT_DIR/../configs/macmini-devserver.yaml}"
sed -e "s|__VENV_PATH__|$VENV_PATH|g" \
    -e "s|__CONFIG_PATH__|$CONFIG_PATH|g" \
    -e "s|__HOME__|$HOME|g" \
    "$AGENT_DIR/templates/$PLIST_NAME.plist" > "$PLIST_DEST"

# 5. Install backup cron
echo "[5/6] Installing backup cron..."
"$QG_HOME/scripts/install-backup-cron.sh"

# 6. Load and start agent
echo "[6/6] Starting agent..."
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "=== Installation complete ==="
echo "Agent status: $(launchctl list | grep $PLIST_NAME || echo 'not found')"
echo "Logs: $QG_HOME/logs/"
echo "Config: $CONFIG_PATH"
```

**Step 3: Create uninstall-agent.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

PLIST_NAME="com.qwickapps.qwickguard-agent"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
QG_HOME="$HOME/.qwickguard"

echo "=== QwickGuard Agent Uninstaller ==="

# 1. Stop and unload LaunchAgent
echo "[1/3] Stopping agent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm -f "$PLIST_PATH"

# 2. Remove backup cron entries
echo "[2/3] Removing cron entries..."
crontab -l 2>/dev/null | grep -v "# QwickGuard" | crontab -

# 3. Remove venv (preserve data)
echo "[3/3] Removing venv..."
rm -rf "$QG_HOME/venv"

echo ""
echo "=== Uninstall complete ==="
echo "Data preserved at: $QG_HOME/"
echo "To remove all data: rm -rf $QG_HOME"
```

**Step 4: Test install on macmini**

```bash
scp -r agent/ macmini-devserver:~/Projects/qwickguard/agent/
ssh macmini-devserver 'cd ~/Projects/qwickguard && agent/scripts/install-agent.sh'
ssh macmini-devserver 'launchctl list | grep qwickguard'
ssh macmini-devserver 'tail -20 ~/.qwickguard/logs/agent.stderr.log'
```

Expected: Agent running, logs showing monitoring cycles.

**Step 5: Commit**

```bash
git add agent/templates/ agent/scripts/install-agent.sh agent/scripts/uninstall-agent.sh
git commit -m "feat: add LaunchAgent plist and install/uninstall scripts

Agent starts on boot, restarts on crash, preserves data on uninstall.

Closes #29"
```

---

## Task 9: Deploy and Verify on macmini

**Step 1: Push code to GitHub**

```bash
git push origin main
```

**Step 2: Pull and install on macmini**

```bash
ssh macmini-devserver 'cd ~/Projects/qwickguard && git pull && agent/scripts/install-agent.sh'
```

**Step 3: Verify agent is running**

```bash
ssh macmini-devserver 'launchctl list | grep qwickguard'
ssh macmini-devserver 'tail -50 ~/.qwickguard/logs/agent.stderr.log'
```

Expected: Agent running, cycles completing every 5 minutes.

**Step 4: Verify healing works**

Watch audit log during a cycle:
```bash
ssh macmini-devserver 'tail -f ~/.qwickguard/logs/audit.log'
```

**Step 5: Update README.md with M3 status**

**Step 6: Close GitHub issues**

```bash
gh issue close 10 -c "M3 complete: Local agent deployed and running on macmini-devserver"
```

**Step 7: Commit README update**

```bash
git add README.md
git commit -m "docs: update README with M3 local agent status"
git push origin main
```

---

## Summary: M3 Task Execution Order

| # | Task | Issue | Priority |
|---|------|-------|----------|
| 1 | Scaffold Python package | #13 | High |
| 2 | System metric collectors | #16 | High |
| 3 | Docker container collectors | #19 | High |
| 4 | Service + process collectors | #22 | High |
| 5 | Llama analyzer | #25 | High |
| 6 | Autonomous healer | #27 | High |
| 7 | Core loop + reporter | #10 | High |
| 8 | LaunchAgent + install script | #29 | High |
| 9 | Deploy and verify on macmini | - | High |

**Estimated total: ~20 hours**

After M3, macmini-devserver has: a fully autonomous monitoring agent running every 5 minutes, collecting system/Docker/service metrics, analyzing via Llama (with threshold fallback), taking safe healing actions (restart containers, kill zombies, prune images), logging all actions to audit trail, and queuing reports for the brain (M4).
