from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

from tactiq.runtime import RequestMetrics, ensure_request_id, is_valid_request_id, log_event


def test_empty_metrics_snapshot_is_json_serializable():
    snapshot = RequestMetrics().snapshot()
    assert snapshot["requests_total"] == 0
    assert snapshot["errors_total"] == 0
    assert snapshot["status_counts"] == {}
    assert snapshot["duration_seconds_p95"] is None
    assert snapshot["duration_seconds_mean"] is None
    json.dumps(snapshot, allow_nan=False)


def test_metrics_have_bounded_samples_and_exact_counters():
    metrics = RequestMetrics(max_samples=3, max_series=2)
    for duration in range(1, 10):
        metrics.record("get", "/matches/{match_id}", 200, duration)
    metrics.record("POST", "/video", 503, 10)
    metrics.record("GET", "/not-found", 404, 11)
    metrics.record("GET", "/exception", 200, 12, error=True)
    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 12
    assert snapshot["errors_total"] == 2
    assert snapshot["http_errors_total"] == 2
    assert snapshot["status_counts"] == {"200": 10, "404": 1, "503": 1}
    assert snapshot["duration_seconds_total"] == 78
    assert snapshot["duration_seconds_mean"] == 6.5
    assert snapshot["duration_seconds_max"] == 12
    assert snapshot["sample_count"] == 3
    assert snapshot["duration_seconds_p50"] == 11
    assert snapshot["duration_seconds_p95"] == pytest.approx(11.9)
    assert len(snapshot["series"]) == 2
    assert sum(item["requests_total"] for item in snapshot["series"]) == 12
    overflow = next(item for item in snapshot["series"] if item["route"] == "<overflow>")
    assert overflow["requests_total"] == 3
    snapshot["series"][0]["requests_total"] = -1
    snapshot["status_counts"].clear()
    assert metrics.snapshot()["status_counts"] == {"200": 10, "404": 1, "503": 1}
    assert all(item["requests_total"] > 0 for item in metrics.snapshot()["series"])


def test_metrics_are_thread_safe_and_label_cardinality_is_bounded():
    metrics = RequestMetrics(max_samples=5, max_series=4)

    def record(worker):
        for _ in range(50):
            metrics.record("GET", f"/template-{worker}", 500 if worker % 2 else 200, 0.25)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(8)))
    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 400
    assert snapshot["errors_total"] == 200
    assert snapshot["status_counts"] == {"200": 200, "500": 200}
    assert snapshot["duration_seconds_total"] == 100
    assert snapshot["sample_count"] == 5
    assert len(snapshot["series"]) <= 4
    assert sum(item["requests_total"] for item in snapshot["series"]) == 400


@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_metrics_limits_are_explicit(value):
    with pytest.raises(ValueError):
        RequestMetrics(max_samples=value)
    with pytest.raises(ValueError):
        RequestMetrics(max_series=value)


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_seconds": -1},
        {"duration_seconds": float("nan")},
        {"duration_seconds": float("inf")},
        {"duration_seconds": "1"},
        {"duration_seconds": True},
        {"status_code": 99},
        {"status_code": 600},
        {"status_code": True},
        {"method": "GET\n"},
        {"route": "/matches?access_token=redacted"},
        {"route": "/matches#fragment"},
        {"route": "/matches\n"},
        {"error": 1},
    ],
)
def test_invalid_observation_does_not_change_totals(changes):
    metrics = RequestMetrics()
    with pytest.raises(ValueError):
        metrics.record(
            **{
                "method": "GET",
                "route": "/matches",
                "status_code": 200,
                "duration_seconds": 0.1,
                **changes,
            }
        )
    assert metrics.snapshot()["requests_total"] == 0


@pytest.mark.parametrize("value", ["request-1", "Trace.ID:0123", "a" * 128])
def test_request_id_preserves_valid_bounded_ids(value):
    assert is_valid_request_id(value)
    assert ensure_request_id(value) == value


@pytest.mark.parametrize("value", [None, "", "unsafe\r\nheader", "a" * 129, "has space", "/path", 1])
def test_request_id_replaces_untrusted_or_missing_ids(value):
    assert not is_valid_request_id(value)
    generated = ensure_request_id(value)
    assert is_valid_request_id(generated)
    assert UUID(generated).version == 4
    assert ensure_request_id(value) != generated


def test_structured_logging_is_allowlisted_and_injection_safe(caplog):
    logger = logging.getLogger("tactiq.runtime.test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(
            logger,
            "request.completed",
            request_id="untrusted\r\nheader",
            tenant_id="tenant-a",
            subject="analyst",
            method="get",
            route="/matches/{match_id}",
            status_code=200,
            duration_seconds=0.25,
            job_id="job-1",
        )
    assert len(caplog.records) == 1
    record = json.loads(caplog.records[0].message)
    assert record["event"] == "request.completed"
    assert record["method"] == "GET"
    assert record["route"] == "/matches/{match_id}"
    assert is_valid_request_id(record["request_id"])
    assert record["request_id"] != "untrusted\r\nheader"
    assert not {"headers", "payload", "body", "authorization", "token"} & set(record)
    with pytest.raises(TypeError):
        log_event(logger, "request.completed", body={"forbidden": "body"})


def test_logging_rejects_unsafe_event_and_raw_url_without_emitting_them(caplog):
    logger = logging.getLogger("tactiq.runtime.test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(ValueError):
            log_event(logger, "bad\ninjection")
        with pytest.raises(ValueError):
            log_event(logger, "request.completed", route="/matches?token=redacted")
    assert not caplog.records
