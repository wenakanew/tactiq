"""Request-scoped accounting from trusted SDK responses, never generated text."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class UsageCapture:
    calls: list[ModelUsage | None] = field(default_factory=list)

    def complete(self, expected_calls: int) -> ModelUsage | None:
        if len(self.calls) != expected_calls or any(call is None for call in self.calls):
            return None
        return ModelUsage(
            sum(call.input_tokens for call in self.calls if call is not None),
            sum(call.output_tokens for call in self.calls if call is not None),
        )


_capture: ContextVar[UsageCapture | None] = ContextVar("tactiq_model_usage", default=None)


@contextmanager
def capture_model_usage() -> Iterator[UsageCapture]:
    capture = UsageCapture()
    token = _capture.set(capture)
    try:
        yield capture
    finally:
        _capture.reset(token)


def record_model_usage(usage: object, *, framework: bool = False) -> None:
    capture = _capture.get()
    if capture is None:
        return
    names = ("input_token_count", "output_token_count", "total_token_count") if framework else (
        "prompt_tokens", "completion_tokens", "total_tokens",
    )
    values = [usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None) for name in names]
    if (all(type(value) is int and value >= 0 for value in values)
            and values[2] > 0 and values[0] + values[1] == values[2]):
        capture.calls.append(ModelUsage(values[0], values[1]))
    else:
        capture.calls.append(None)
