"""System metric collector using psutil.

Collects CPU, RAM, disk, load average, open file handles, uptime, and
optionally temperature from hardware sensors.
"""

from __future__ import annotations

import time

import psutil

from ..models import SystemMetrics


async def collect_system_metrics() -> SystemMetrics:
    """Collect a snapshot of system-level resource metrics.

    Returns:
        SystemMetrics: Current host resource usage, rounded to reasonable precision.

    Notes:
        - cpu_percent uses a 1-second blocking interval for accuracy.
        - temperature is None on platforms that do not expose sensor data (e.g. macOS).
        - open_files falls back to 0 if the process cannot enumerate its handles.
    """
    cpu_percent = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()

    try:
        open_files = len(psutil.Process().open_files())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        open_files = 0

    uptime = time.time() - psutil.boot_time()

    temperature: float | None = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for _name, entries in temps.items():
                if entries:
                    temperature = entries[0].current
                    break
    except (AttributeError, RuntimeError):
        pass  # macOS often doesn't expose sensor data

    return SystemMetrics(
        cpu_percent=round(cpu_percent, 1),
        ram_percent=round(mem.percent, 1),
        ram_available_gb=round(mem.available / (1024**3), 2),
        disk_percent=round(disk.percent, 1),
        disk_available_gb=round(disk.free / (1024**3), 2),
        load_avg=(round(load[0], 2), round(load[1], 2), round(load[2], 2)),
        open_files=open_files,
        uptime_seconds=round(uptime),
        temperature=temperature,
    )
