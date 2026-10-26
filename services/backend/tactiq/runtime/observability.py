"""Bounded, dependency-free request metrics and allowlisted structured logs.

Pass route templates (e.g. /matches/{match_id}), never raw URLs, query strings,
request bodies or tokens. Metrics are process-local and reset on restart.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ._validation import identifier, positive_integer


_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}\Z")
_METHOD = re.compile(r"[A-Z]{1,16}\Z")
_EVENT = re.compile(r"[A-Za-z0-9_.:\-]{1,80}\Z")
_OVERFLOW = ("OTHER", "<overflow>")


def is_valid_request_id(value: str | None) -> bool:
    return isinstance(value, str) and _REQUEST_ID.fullmatch(value) is not None


def ensure_request_id(value: str | None = None) -> str:
    """Preserve a bounded safe ID; replace absent/untrusted IDs with a fresh UUID."""
    return value if is_valid_request_id(value) else uuid4().hex


def _labels(method: str, route: str) -> tuple[str, str]:
    if not isinstance(method, str) or not _METHOD.fullmatch(method.upper()):
        raise ValueError("method must be a bounded HTTP method")
    identifier(route, "route", max_length=256)
    if "?" in route or "#" in route:
        raise ValueError("Use route templates, not URLs or query strings")
    return method.upper(), route


def _duration(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("duration_seconds must be a nonnegative finite number")
    return float(value)


def _status(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ValueError("status_code must be an integer between 100 and 599")
    return value


def _percentile(samples: list[float], fraction: float) -> float | None:
    if not samples:
        return None
    position = (len(samples) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return samples[lower] + (samples[upper] - samples[lower]) * (position - lower)


class RequestMetrics:
    """Thread-safe totals, status counts and a rolling bounded latency sample.

    errors_total counts 5xx responses or explicitly flagged exceptions. All
    4xx/5xx responses also contribute to http_errors_total. max_series includes
    one reserved overflow bucket, preventing unbounded route-cardinality growth.
    Percentiles are over the rolling sample, not an all-time histogram.
    """

    def __init__(self, max_samples: int = 256, max_series: int = 128) -> None:
        positive_integer(max_samples, "max_samples")
        positive_integer(max_series, "max_series")
        self._lock = threading.Lock()
        self._samples: deque[float] = deque(maxlen=max_samples)
        self._max_series = max_series
        self._total = 0
        self._errors = 0
        self._http_errors = 0
        self._duration_total = 0.0
        self._duration_max = 0.0
        self._statuses: Counter[int] = Counter()
        self._series: dict[tuple[str, str], dict[str, int | float]] = {}

    def record(
        self,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
        *,
        error: bool = False,
    ) -> None:
        labels = _labels(method, route)
        status = _status(status_code)
        duration = _duration(duration_seconds)
        if not isinstance(error, bool):
            raise ValueError("error must be a boolean")
        failed = error or status >= 500
        with self._lock:
            if not math.isfinite(self._duration_total + duration):
                raise ValueError("Accumulated request duration is too large")
            if labels not in self._series and len(self._series) >= self._max_series - 1:
                labels = _OVERFLOW
            series = self._series.setdefault(
                labels, {"requests_total": 0, "errors_total": 0, "duration_seconds_total": 0.0}
            )
            series["requests_total"] += 1
            series["errors_total"] += int(failed)
            series["duration_seconds_total"] += duration
            self._total += 1
            self._errors += int(failed)
            self._http_errors += int(status >= 400)
            self._duration_total += duration
            self._duration_max = max(self._duration_max, duration)
            self._statuses[status] += 1
            self._samples.append(duration)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            samples = sorted(self._samples)
            result = {
                "requests_total": self._total,
                "errors_total": self._errors,
                "http_errors_total": self._http_errors,
                "status_counts": {str(code): count for code, count in sorted(self._statuses.items())},
                "duration_seconds_total": self._duration_total,
                "duration_seconds_max": self._duration_max,
                "duration_seconds_mean": self._duration_total / self._total if self._total else None,
                "sample_count": len(samples),
                "series": [
                    {"method": method, "route": route, **values}
                    for (method, route), values in sorted(self._series.items())
                ],
            }
        result["duration_seconds_p50"] = _percentile(samples, 0.5)
        result["duration_seconds_p95"] = _percentile(samples, 0.95)
        result["duration_seconds_p99"] = _percentile(samples, 0.99)
        return result


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    request_id: str | None = None,
    tenant_id: str | None = None,
    subject: str | None = None,
    method: str | None = None,
    route: str | None = None,
    status_code: int | None = None,
    duration_seconds: float | None = None,
    job_id: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Emit only allowlisted metadata; arbitrary request/error payloads are unsupported."""
    if not isinstance(event, str) or not _EVENT.fullmatch(event):
        raise ValueError("event must be a bounded structured log event name")
    record: dict[str, Any] = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if request_id is not None:
        record["request_id"] = ensure_request_id(request_id)
    for name, value in (("tenant_id", tenant_id), ("subject", subject), ("job_id", job_id)):
        if value is not None:
            record[name] = identifier(value, name)
    if method is not None or route is not None:
        normalized_method, normalized_route = _labels(
            method if method is not None else "OTHER",
            route if route is not None else "<unmatched>",
        )
        if method is not None:
            record["method"] = normalized_method
        if route is not None:
            record["route"] = normalized_route
    if status_code is not None:
        record["status_code"] = _status(status_code)
    if duration_seconds is not None:
        record["duration_seconds"] = _duration(duration_seconds)
    logger.log(level, json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
