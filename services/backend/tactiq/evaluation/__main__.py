from __future__ import annotations

import argparse
import json
from pathlib import Path

from tactiq.cv.types import CameraCalibration, CvTrackPoint
from tactiq.evaluation.gates import release_gates
from tactiq.evaluation.tracking import AnnotatedFrame, evaluate_calibration, evaluate_tracking
from tactiq.evaluation.value import LabelledAction, fit_value_model, score_value_model
from tactiq.evaluation.narrative import evaluate_narratives
from tactiq.intelligence.value import ValueModel
from tactiq.evaluation.smoke import run_smoke
from tactiq.evaluation.insights import LabelledInsightWindow, evaluate_insights
from tactiq.domain.models import Insight


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate annotated tracking output or enforce production gates.")
    commands = parser.add_subparsers(dest="command", required=True)
    tracking = commands.add_parser("tracking")
    tracking.add_argument("--annotations", type=Path, required=True)
    tracking.add_argument("--predictions", type=Path, required=True)
    tracking.add_argument("--output", type=Path, required=True)
    gate = commands.add_parser("gate")
    gate.add_argument("--report", type=Path, required=True)
    gate.add_argument("--output", type=Path)
    train = commands.add_parser("train-value")
    train.add_argument("--dataset", type=Path, required=True)
    train.add_argument("--version", required=True)
    train.add_argument("--output", type=Path, required=True)
    score = commands.add_parser("score-value")
    score.add_argument("--dataset", type=Path, required=True)
    score.add_argument("--model", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    narrative = commands.add_parser("narrative")
    narrative.add_argument("--dataset", type=Path, required=True)
    narrative.add_argument("--output", type=Path, required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--output", type=Path, required=True)
    insights = commands.add_parser("insights")
    insights.add_argument("--dataset", type=Path, required=True)
    insights.add_argument("--predictions", type=Path, required=True)
    insights.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "insights":
        labels = json.loads(args.dataset.read_text(encoding="utf-8"))
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        result = evaluate_insights(
            [LabelledInsightWindow.model_validate(value) for value in labels["windows"]],
            [Insight.model_validate(value) for value in predictions["insights"]],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if args.command == "smoke":
        result = run_smoke()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"smoke_passed": result["smoke_passed"],
                          "production_ready": result["production_gates"]["production_ready"]}))
        return 0 if result["smoke_passed"] else 1
    if args.command in {"train-value", "score-value", "narrative"}:
        data = json.loads(args.dataset.read_text(encoding="utf-8"))
        if args.command == "narrative":
            result = evaluate_narratives(data["cases"])
        else:
            actions = [LabelledAction.model_validate(action) for action in data["actions"]]
            if args.command == "train-value":
                result = fit_value_model(actions, args.version, data["dataset"]["source"]).model_dump()
            else:
                model = ValueModel.model_validate_json(args.model.read_text(encoding="utf-8"))
                result = score_value_model(model, actions)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    if args.command == "tracking":
        annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        result = {
            "schema_version": "1.0.0",
            "dataset": annotations["dataset"],
            "model_id": predictions.get("model_id"),
            "tracking": evaluate_tracking(
                [AnnotatedFrame.model_validate(frame) for frame in annotations["frames"]],
                [CvTrackPoint.model_validate(point) for point in predictions["tracks"]],
            ),
        }
        if annotations.get("holdout_landmarks"):
            result["calibration"] = evaluate_calibration(
                CameraCalibration.model_validate(predictions["calibration"]), annotations["holdout_landmarks"]
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    report = json.loads(args.report.read_text(encoding="utf-8"))
    result = release_gates(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["production_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
