"""Opt-in, bounded live checks. Reports never contain credentials or response bodies."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--allow-billable", action="store_true", required=True)
    args = parser.parse_args()
    from dotenv import dotenv_values

    for name, value in dotenv_values(args.env_file).items():
        if value is not None:
            os.environ[name] = value
    root = args.output_directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "APP_ENV": "local", "TACTIQ_AUTH_ENABLED": "false",
        "TACTIQ_MEDIA_DIR": str(root / "media"), "TACTIQ_DATABASE_PATH": str(root / "matches.sqlite3"),
        "TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE": "20",
    })
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from tactiq.presentation.api import main as api

    report: dict = {"basis": "live-existing-resources/synthetic-inputs", "operations": [], "profile_lookup": []}
    errors: list[dict] = []
    original_call = api.service.narrative_generator._call_foundry_with_system

    def observed_model_call(*arguments, **keywords):
        try:
            return original_call(*arguments, **keywords)
        except Exception as error:
            errors.append({"error_type": type(error).__name__, "http_status": getattr(error, "status_code", None)})
            raise

    api.service.narrative_generator._call_foundry_with_system = observed_model_call
    with TestClient(api.app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/matches", json={"scenario_id": "sustained_pressure", "seed": 42001})
        if created.status_code != 200:
            report["setup"] = {"status": "failed", "http_status": created.status_code}
        else:
            match_id = created.json()["match_id"]
            client.post(f"/api/v1/matches/{match_id}/start")
            for _ in range(40):
                insights = client.get(f"/api/v1/matches/{match_id}/insights").json()
                if any(item["type"] == "sustained_pressure" for item in insights):
                    break
                time.sleep(0.1)
            insight = next((item for item in insights if item["type"] == "sustained_pressure"), None)
            if insight is None:
                report["setup"] = {"status": "failed", "reason": "synthetic_insight_unavailable"}
            else:
                blocked = None
                operations = (
                    ("narrative", "narratives", {"audience_mode": "analyst", "language": "en-GB", "insight_id": insight["insight_id"]}),
                    ("recap", "recap", {"audience_mode": "fan", "language": "en-GB"}),
                    ("dual_commentary", "commentary/dual", {"language": "en-GB", "insight_id": insight["insight_id"]}),
                )
                for name, route, payload in operations:
                    if blocked:
                        report["operations"].append({"operation": name, "status": "blocked", "reason": blocked})
                        continue
                    before = len(errors)
                    started = time.monotonic()
                    response = client.post(f"/api/v1/matches/{match_id}/{route}", json=payload)
                    data = response.json()
                    provider = data.get("provider", "")
                    model_errors = errors[before:]
                    status = "passed" if response.status_code == 200 and provider.startswith("foundry") and not data.get("fallback_used") else "failed"
                    report["operations"].append({
                        "operation": name, "status": status, "http_status": response.status_code,
                        "provider": provider, "fallback_used": data.get("fallback_used"),
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "errors": model_errors,
                        "grounding_status": (data.get("grounding") or {}).get("status"),
                    })
                    if any(item["http_status"] in {401, 403, 404, 429} for item in model_errors):
                        blocked = "previous_model_authentication_deployment_or_quota_failure"
                audit = client.get(f"/api/v1/matches/{match_id}/commentary/debug?limit=1")
                if audit.status_code == 200:
                    for entry in audit.json().get("entries", []):
                        report["profile_lookup"] = [
                            {key: agent.get(key) for key in ("name", "version", "source")}
                            for agent in entry.get("audit", {}).get("agents", [])
                        ]
                primary = os.environ.get("AZURE_SPEECH_COMMENTATOR_A_VOICE") or os.environ.get("AZURE_SPEECH_DEFAULT_VOICE")
                speech_operations = (
                    ("single_speech", "synthesize", {"text": "The supported pressure pattern is developing.", "language": "en-GB", "voice": primary}),
                    ("dual_speech", "synthesize-dual", {
                        "primary_text": "The pressure is building in this phase.",
                        "secondary_text": "The measured progression supports that reading.", "language": "en-GB",
                    }),
                )
                for name, route, payload in speech_operations:
                    if blocked == "previous_speech_failure":
                        report["operations"].append({"operation": name, "status": "blocked", "reason": blocked})
                        continue
                    started = time.monotonic()
                    response = client.post(f"/api/v1/speech/{route}", json=payload)
                    data = response.json()
                    result = {
                        "operation": name, "status": "failed", "http_status": response.status_code,
                        "provider": data.get("provider"), "duration_ms": round((time.monotonic() - started) * 1000),
                        "explicit_request_voice": name == "single_speech",
                    }
                    if response.status_code == 200:
                        try:
                            content = base64.b64decode(data["audio_base64"], validate=True)
                            target = root / f"{name}.mp3"
                            target.write_bytes(content)
                            binary = shutil.which("ffprobe")
                            if binary is None:
                                result["reason"] = "ffprobe_unavailable"
                            else:
                                probe = subprocess.run(
                                    [binary, "-v", "error", "-show_entries", "format=duration",
                                     "-of", "default=noprint_wrappers=1:nokey=1", str(target)],
                                    capture_output=True, text=True, timeout=10, check=False,
                                )
                                duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0
                                result.update({"status": "passed" if duration > 0 else "failed",
                                               "audio_bytes": len(content), "audio_duration_ms": round(duration * 1000)})
                        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
                            result["error_type"] = type(error).__name__
                    else:
                        blocked = "previous_speech_failure"
                    report["operations"].append(result)
    report["passed"] = bool(report.get("operations")) and all(item["status"] == "passed" for item in report["operations"])
    target = root / "live-services.json"
    target.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
