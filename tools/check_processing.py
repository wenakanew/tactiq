"""Bounded deterministic processing check, not a real-football/model benchmark."""

import argparse
import json
from pathlib import Path
from statistics import mean
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=120)
    args = parser.parse_args()
    if not 20 <= args.frames <= 1000:
        parser.error("--frames must be in [20, 1000]")
    from tactiq.domain.models import Event, MatchState
    from tactiq.intelligence.engine import detect_insights, reduce_match_state
    from tactiq.intelligence.value import ValueModel

    state = MatchState(match_id="processing-fixture", scenario_id="generated-tracking", seed=42)
    history = []
    insights = []
    durations = []
    for index in range(args.frames):
        players = [
            {
                "track_id": f"{team}_{player}", "team_id": team,
                "position": {"x": (15 + player * 4 if team == "team_a" else 85 - player * 4) + index * 0.01,
                             "y": 10 + player * 4},
                "confidence": 1.0, "inferred": False, "speed_kmh": 0.3024 if index else None,
            }
            for team in ("team_a", "team_b") for player in range(11)
        ]
        event = Event(
            match_id=state.match_id, event_id=f"generated-{index}", sequence=index + 1,
            match_clock_ms=index * 125, type="tracking_frame", team_id="team_a",
            qualifiers={"cv_frame": {
                "tracking_source": "generated-benchmark-fixture", "detection_source": "synthetic",
                "calibrated": True, "calibration": {"revision": 1},
                "players": players, "ball_position": {"x": 50, "y": 50},
                "possession_team_id": "team_a",
            }},
        )
        history.append(event)
        started = time.perf_counter()
        reduce_match_state(state, event, history, ValueModel.reference())
        insights.extend(detect_insights(state, history, insights))
        durations.append((time.perf_counter() - started) * 1000)
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    result = {
        "basis": "generated-calibrated-tracking-fixture",
        "scope": "reducer, metrics, tactical snapshots and detectors; excludes decoding/inference/storage/cloud/network",
        "frames": args.frames, "players_per_frame": 22,
        "simulated_window_ms": history[-1].match_clock_ms,
        "mean_processing_ms": round(mean(durations), 3),
        "p95_processing_ms": round(p95, 3), "max_processing_ms": round(max(durations), 3),
        "check_threshold_ms": 100, "passed": p95 <= 100,
        "production_load_certified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
