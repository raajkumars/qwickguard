"""Metric collector modules for the QwickGuard agent.

Each sub-module is responsible for gathering one category of metrics:
- system: CPU, RAM, disk, load average, open files, uptime, temperature
- containers: Docker container status and resource usage
- services: HTTP health checks for monitored service endpoints
- processes: OS process liveness checks
"""
