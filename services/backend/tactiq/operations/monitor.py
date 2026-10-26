from __future__ import annotations

import math
from urllib.parse import urlsplit

import requests


def slo_status(http_metrics: dict, pipeline_metrics: dict, *, min_samples: int = 20,
               http_p95_seconds: float = 4.0, error_rate: float = 0.02, job_metrics: dict | None = None) -> dict:
    if min_samples < 1 or not math.isfinite(http_p95_seconds) or http_p95_seconds <= 0 or not 0 <= error_rate <= 1:
        raise ValueError("Invalid SLO thresholds")
    requests_total = int(http_metrics.get("requests_total", 0))
    errors = int(http_metrics.get("errors_total", 0))
    measured = requests_total >= min_samples
    p95 = http_metrics.get("duration_seconds_p95")
    ratio = errors / requests_total if requests_total else None
    alerts = []
    if measured and ratio is not None and ratio > error_rate:
        alerts.append({"name": "api_error_budget", "severity": "high", "measured": ratio, "threshold": error_rate})
    if measured and isinstance(p95, (float, int)) and p95 > http_p95_seconds:
        alerts.append({"name": "api_latency", "severity": "medium", "measured": p95, "threshold": http_p95_seconds})
    jobs = job_metrics or {}
    if jobs.get("errors_total", 0):
        alerts.append({"name": "analysis_job_failure", "severity": "high", "measured": jobs["errors_total"], "threshold": 0})
    return {
        "status": "alerting" if alerts else "within_threshold" if measured else "insufficient_samples",
        "sample_requirement": min_samples, "http": http_metrics, "pipeline": pipeline_metrics,
        "jobs": jobs, "alerts": alerts,
    }


def send_alerts(webhook_url: str, alerts: list[dict]) -> None:
    parsed = urlsplit(webhook_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Alert webhooks must use HTTPS without embedded credentials")
    if not alerts:
        return
    try:
        response = requests.post(webhook_url, json={"service": "tactiq", "alerts": alerts}, timeout=10, allow_redirects=False)
    except requests.RequestException:
        raise RuntimeError("Alert delivery is unavailable") from None
    try:
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Alert delivery failed with HTTP {response.status_code}")
    finally:
        response.close()


def prometheus_metrics(http: dict, pipeline: dict) -> str:
    lines = []
    for prefix, metrics in (("http", http), ("pipeline", pipeline)):
        for name in ("requests_total", "errors_total", "duration_seconds_p95", "duration_seconds_max"):
            value = metrics.get(name)
            if isinstance(value, (int, float)) and math.isfinite(value):
                lines.append(f"tactiq_{prefix}_{name} {value}")
    return "\n".join(lines) + "\n"
