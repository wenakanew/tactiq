"""Shared validation for persisted, JSON-only runtime records."""

from __future__ import annotations

import json
import math
from typing import Any


class RecordValidationError(ValueError):
    """A runtime record is not a valid identifier or JSON object."""


_CREDENTIAL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "apikey",
        "authorization",
        "bearer_token",
        "client_secret",
        "cookie",
        "id_token",
        "password",
        "proxy_authorization",
        "refresh_token",
        "secret_key",
        "set_cookie",
        "token",
        "token_sha256",
    }
)


def identifier(value: str, label: str, *, max_length: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RecordValidationError(f"{label} must be a nonempty, bounded identifier")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise RecordValidationError(f"{label} must be valid UTF-8") from exc
    return value


def positive_integer(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RecordValidationError(f"{label} must be a positive integer")
    return value


def _validate_tree(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise RecordValidationError("JSON records cannot exceed 64 nesting levels")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecordValidationError("JSON records must contain only finite numbers")
        return
    if isinstance(value, list):
        for item in value:
            _validate_tree(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RecordValidationError("JSON object keys must be strings")
            if key.casefold().replace("-", "_") in _CREDENTIAL_KEYS:
                raise RecordValidationError("Credentials must not be persisted in runtime records")
            _validate_tree(item, depth + 1)
        return
    raise RecordValidationError("Runtime records must contain only JSON values")


def encode_object(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise RecordValidationError("A runtime record must be a JSON object")
    _validate_tree(value)
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RecordValidationError("A runtime record must be valid UTF-8 JSON") from exc


def decode_object(value: str) -> dict[str, Any]:
    def reject_constant(_: str) -> None:
        raise RecordValidationError("JSON records must contain only finite numbers")

    try:
        decoded = json.loads(value, parse_constant=reject_constant)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecordValidationError("Stored record is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RecordValidationError("Stored record is not a JSON object")
    _validate_tree(decoded)
    return decoded
