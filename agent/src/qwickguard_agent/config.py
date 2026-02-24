"""Configuration loading for QwickGuard agent.

Reads a YAML config file whose path is determined by the QWICKGUARD_CONFIG
environment variable.  Defaults to the repo-level configs/macmini-devserver.yaml
resolved relative to this file's location.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class ThresholdConfig(BaseModel):
    """Numeric thresholds for warning and critical alerts."""

    cpu_warning: float = 80.0
    cpu_critical: float = 95.0
    ram_warning: float = 85.0
    ram_critical: float = 95.0
    disk_warning: float = 80.0
    disk_critical: float = 90.0


class ContainerConfig(BaseModel):
    """Configuration for a single monitored Docker container."""

    name: str
    critical: bool = False
    compose_file: Optional[str] = None


class ServiceConfig(BaseModel):
    """Configuration for a single monitored HTTP service endpoint."""

    name: str
    url: str
    critical: bool = False


class BackupConfig(BaseModel):
    """Configuration for a single database backup job."""

    name: str
    container: str
    command: str
    schedule: str
    retention_days: int = 14


class ServerConfig(BaseModel):
    """Top-level configuration for a QwickGuard-managed server."""

    hostname: str
    agent_id: str
    brain_url: str
    compute_worker_url: str
    check_interval_seconds: int = 300
    thresholds: ThresholdConfig = ThresholdConfig()
    containers: list[ContainerConfig] = []
    services: list[ServiceConfig] = []
    backups: list[BackupConfig] = []
    github_runners: list[str] = []
    zombie_patterns: list[str] = []


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

# Default path: <repo-root>/configs/macmini-devserver.yaml
# This file lives at <repo-root>/agent/src/qwickguard_agent/config.py,
# so three parents up gives the repo root.
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "configs" / "macmini-devserver.yaml"


def load_config(path: Optional[str | Path] = None) -> ServerConfig:
    """Load and validate the YAML configuration file.

    Resolution order for the config file path:
    1. Explicit ``path`` argument (if supplied).
    2. ``QWICKGUARD_CONFIG`` environment variable.
    3. Default: ``<repo-root>/configs/macmini-devserver.yaml``.

    Args:
        path: Optional explicit path to the YAML config file.

    Returns:
        A validated :class:`ServerConfig` instance.

    Raises:
        FileNotFoundError: If the resolved config file does not exist.
        yaml.YAMLError: If the file contains invalid YAML.
        pydantic.ValidationError: If the YAML structure does not match the schema.
    """
    if path is not None:
        config_path = Path(path)
    else:
        env_path = os.environ.get("QWICKGUARD_CONFIG")
        config_path = Path(env_path) if env_path else _DEFAULT_CONFIG_PATH

    if not config_path.exists():
        raise FileNotFoundError(
            f"QwickGuard config file not found: {config_path}"
        )

    with config_path.open("r") as fh:
        raw = yaml.safe_load(fh)

    return ServerConfig.model_validate(raw)
