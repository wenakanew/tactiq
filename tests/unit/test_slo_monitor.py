import pytest

from tactiq.operations.monitor import prometheus_metrics, send_alerts, slo_status


def test_slos_distinguish_missing_measurements_from_good_health():
    assert slo_status({}, {})["status"] == "insufficient_samples"
    assert slo_status({"requests_total": 20, "errors_total": 0, "duration_seconds_p95": 0.2}, {})["status"] == "within_threshold"
    status = slo_status({"requests_total": 20, "errors_total": 2, "duration_seconds_p95": 8}, {})
    assert {item["name"] for item in status["alerts"]} == {"api_error_budget", "api_latency"}


def test_alert_delivery_failure_is_not_silently_ignored(monkeypatch):
    class Rejected:
        status_code = 500
        def close(self):
            pass
    monkeypatch.setattr("tactiq.operations.monitor.requests.post", lambda *args, **kwargs: Rejected())
    with pytest.raises(RuntimeError, match="500"):
        send_alerts("https://monitor.example/alerts", [{"name": "api_latency", "measured": 8}])
    with pytest.raises(ValueError):
        send_alerts("http://monitor.example/alerts", [])


def test_prometheus_output_contains_only_aggregate_measurements():
    output = prometheus_metrics({"requests_total": 25, "duration_seconds_p95": 0.1, "token": "never-export"}, {})
    assert "tactiq_http_requests_total 25" in output
    assert "never-export" not in output
