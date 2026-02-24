"""Pydantic models for QwickGuard agent data structures."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SystemMetrics(BaseModel):
    """System-level resource metrics collected from the host."""

    cpu_percent: float
    ram_percent: float
    ram_available_gb: float
    disk_percent: float
    disk_available_gb: float
    load_avg: tuple[float, float, float]
    open_files: int
    uptime_seconds: float
    temperature: Optional[float] = None


class ContainerStatus(BaseModel):
    """Runtime status of a single Docker container."""

    name: str
    status: str
    health: str
    restart_count: int
    cpu_percent: float
    memory_mb: float
    uptime_seconds: float


class ServiceHealth(BaseModel):
    """HTTP health check result for a monitored service endpoint."""

    name: str
    url: str
    healthy: bool
    response_time_ms: float
    error: Optional[str] = None


class ProcessInfo(BaseModel):
    """Liveness state of a tracked OS process."""

    name: str
    pid: Optional[int] = None
    pattern: str
    alive: bool


class CollectedMetrics(BaseModel):
    """Full snapshot of all metrics gathered in a single collection cycle."""

    system: SystemMetrics
    containers: list[ContainerStatus]
    services: list[ServiceHealth]
    processes: list[ProcessInfo]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AnalysisResult(BaseModel):
    """Output of the agent's rule-based analysis of collected metrics."""

    status: Literal["healthy", "warning", "critical"]
    issues: list[str]
    actions: list[dict]
    escalate_to_claude: bool


class ActionResult(BaseModel):
    """Record of a single remediation action attempted by the agent."""

    action: str
    target: str
    reason: str
    decided_by: str
    result: Literal["success", "failed", "rejected", "cooldown", "skipped"]
    error: Optional[str] = None


class AgentReport(BaseModel):
    """Complete report produced at the end of a single agent cycle."""

    agent_id: str
    hostname: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metrics: CollectedMetrics
    analysis: AnalysisResult
    actions_taken: list[ActionResult]
