# Runbook: Beszel Alert Configuration

**Issue:** #12
**Date configured:** 2026-02-23
**UI:** http://macmini-devserver:8090

---

## Overview

Beszel provides built-in alerting for system metrics and container health. Alerts are configured through the Beszel web UI under each system's settings.

---

## Recommended Alert Thresholds

Configure these in Beszel UI > macmini-devserver > Alerts:

### System Metrics

| Metric | Warning | Critical | Notes |
|--------|---------|----------|-------|
| CPU | 80% sustained 5 min | 95% sustained 2 min | Matches configs/macmini-devserver.yaml |
| Memory | 85% | 95% | Matches configs/macmini-devserver.yaml |
| Disk | 80% | 90% | Matches configs/macmini-devserver.yaml |

### Container Alerts

Monitor these critical containers for down/unhealthy status:

| Container | Priority | Alert On |
|-----------|----------|----------|
| faabzi-postgres | Critical | Down or unhealthy |
| qwickbrain-server | Critical | Down or unhealthy |
| qwickbrain-node | Critical | Down or unhealthy |
| qwickbrain-postgres | Critical | Down or unhealthy |
| qwickbrain-redis | Critical | Down or unhealthy |
| qwickbrain-qdrant | Critical | Down or unhealthy |
| qwickbrain-neo4j | Critical | Down or unhealthy |

---

## Notification Channels

Beszel supports webhooks for alert delivery. Configure under Settings > Notifications.

### Current Configuration

- **In-app notifications:** Enabled (default)
- **Webhook (Slack/Discord):** TBD - configure when webhook URL is available

### Adding a Slack Webhook

1. Create a Slack incoming webhook at https://api.slack.com/messaging/webhooks
2. In Beszel: Settings > Notifications > Add webhook
3. Enter the webhook URL
4. Test with a manual alert trigger

### Adding a Discord Webhook

1. In Discord: Channel Settings > Integrations > Webhooks > New Webhook
2. Copy the webhook URL
3. In Beszel: Settings > Notifications > Add webhook
4. Append `/slack` to the Discord webhook URL (Discord accepts Slack-format webhooks)

---

## Managing Alerts

### Viewing Active Alerts

Open Beszel UI > Dashboard. Active alerts show as colored indicators on the system card.

### Acknowledging Alerts

Click on the alert in the UI to view details. Alerts auto-resolve when the metric returns below threshold.

### Modifying Thresholds

1. Open Beszel UI > macmini-devserver > Settings
2. Adjust threshold values
3. Save

---

## Audit Frequency

Re-review alert configuration:

- After adding new containers
- After infrastructure changes (new services, capacity changes)
- Monthly during infrastructure reviews
- After any alert fatigue (too many false positives = adjust thresholds)
