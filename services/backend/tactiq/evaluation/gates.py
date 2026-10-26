from __future__ import annotations

from typing import Any


def release_gates(report: dict[str, Any]) -> dict:
    checks = []

    def check(name: str, passed: bool, reason: str) -> None:
        checks.append({"gate": name, "passed": bool(passed), "reason": reason})

    metadata = report.get("dataset", {})
    check("licensed_football_validation", metadata.get("domain") == "licensed-football"
          and bool(metadata.get("license_reference")) and bool(metadata.get("reviewer")),
          "Real-world CV readiness requires licensed, independently annotated football validation data.")
    tracking = report.get("tracking", {})
    check("tracking_window", tracking.get("duration_ms", 0) >= 15_000, "Tracking must be measured across at least 15 seconds.")
    consistency = tracking.get("player_id_consistency")
    check("player_identity", consistency is not None and consistency >= 0.9, "Player identity consistency must reach 90%.")
    teams = tracking.get("team_classification_accuracy")
    check("team_identification", teams is not None and teams >= 0.95, "Team classification accuracy must reach 95%.")
    recovery = tracking.get("ball_recovery_max_ms")
    check("ball_occlusion_recovery", tracking.get("occlusion_cases", 0) > 0 and recovery is not None and recovery <= 1500
          and tracking.get("unrecovered_occlusions", 1) == 0, "Ball recovery must be measured and bounded by 1500 ms.")
    calibration = report.get("calibration", {})
    error = calibration.get("rmse_m")
    check("camera_calibration", calibration.get("holdout_count", 0) >= 4 and calibration.get("coverage", 0) == 1
          and error is not None and error <= 1, "Four independent holdout landmarks must project with RMSE <= 1 metre.")
    narrative = report.get("narrative", {})
    check("narrative_faithfulness", narrative.get("evaluated_cases", 0) >= 20
          and narrative.get("unsupported_claims", 1) == 0 and bool(narrative.get("reviewed_by"))
          and bool(narrative.get("deployment_id")),
          "At least 20 reviewed outputs from the target deployment, with no unsupported claims.")
    tactics = report.get("tactics", {})
    check("tactical_validation", tactics.get("labelled_windows", 0) >= 20
          and tactics.get("precision", 0) >= 0.9 and tactics.get("recall", 0) >= 0.9
          and bool(tactics.get("reviewed_by")),
          "Reviewed tactical windows must reach 90% precision and recall.")
    localization = report.get("localization", {})
    required = {"en-GB", "sw-KE", "fr-FR", "es-ES", "pt-BR", "ar-SA", "hi-IN"}
    check("multilingual_review", required <= set(localization.get("reviewed_locales", []))
          and localization.get("entity_number_preservation", 0) == 1,
          "Every enabled locale needs domain review and complete entity/number preservation.")
    replay = report.get("replay", {})
    sync = replay.get("max_sync_error_ms")
    check("replay_sync", sync is not None and sync < 150 and replay.get("measured_video_seconds", 0) >= 15,
          "Measure video/telemetry synchronization below 150 ms over a representative clip.")
    platform = report.get("platform", {})
    check("platform_readiness", all(platform.get(key) is True for key in (
        "tenant_isolation_verified", "security_review_passed", "staging_alerts_verified", "restore_verified"
    )), "Access isolation, security review, restore, and staging alert delivery must all be verified.")
    value = report.get("value_model", {})
    check("value_model_validation", value.get("validated") is True and bool(value.get("validation_reference")),
          "xT/xG estimates require a separate held-out calibration report before production.")
    return {"production_ready": all(item["passed"] for item in checks), "checks": checks}
