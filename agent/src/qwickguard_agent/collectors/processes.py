"""Process liveness collector using psutil.

Checks two categories of processes:

1. GitHub runner processes: verifies that at least one running process has
   a configured runner path in its command-line arguments.
2. Zombie processes: detects processes matching configurable name patterns
   that should not be running.
"""

from __future__ import annotations

import psutil

from ..config import ServerConfig
from ..models import ProcessInfo


async def collect_process_info(config: ServerConfig) -> list[ProcessInfo]:
    """Collect liveness information for GitHub runners and zombie processes.

    Iterates all running OS processes once using ``psutil.process_iter`` and
    checks each process against the configured runner paths and zombie
    patterns.

    **GitHub runner detection:** For each path in ``config.github_runners``,
    scans all processes to see whether any process has that path present in
    its command-line arguments. Reports ``alive=True`` if found, ``alive=False``
    otherwise.

    **Zombie detection:** For each pattern in ``config.zombie_patterns``,
    reports a :class:`ProcessInfo` entry (``alive=True``) for every process
    whose command line contains the pattern string.

    Args:
        config: Server configuration with ``github_runners`` paths and
            ``zombie_patterns`` strings.

    Returns:
        A list of :class:`ProcessInfo` instances. Runner entries are prefixed
        with ``"runner:"``, zombie entries with ``"zombie:"``. Returns an
        empty list if neither list is configured.

    Notes:
        - ``psutil.NoSuchProcess`` and ``psutil.AccessDenied`` are silently
          ignored; the process is skipped.
        - Runner ``pid`` is ``None`` because the check is aggregate (any
          process with that path counts).
    """
    if not config.github_runners and not config.zombie_patterns:
        return []

    # Snapshot all running processes once to avoid repeated iteration.
    running_processes: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "cmdline", "status"]):
        try:
            proc.info  # trigger attribute fetch; raises if process gone
            running_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    results: list[ProcessInfo] = []

    # --- GitHub runner detection ---
    for runner_path in config.github_runners:
        alive = False
        for proc in running_processes:
            try:
                cmdline = proc.info.get("cmdline") or []
                if any(runner_path in arg for arg in cmdline):
                    alive = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        results.append(
            ProcessInfo(
                name=f"runner:{runner_path}",
                pid=None,
                pattern=runner_path,
                alive=alive,
            )
        )

    # --- Zombie / unexpected process detection ---
    for pattern in config.zombie_patterns:
        for proc in running_processes:
            try:
                cmdline = proc.info.get("cmdline") or []
                if any(pattern in arg for arg in cmdline):
                    results.append(
                        ProcessInfo(
                            name=f"zombie:{pattern}",
                            pid=proc.info.get("pid"),
                            pattern=pattern,
                            alive=True,
                        )
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    return results
