import pytest

from tactiq.cv.calibration import fit_calibration
from tactiq.cv.types import CalibrationInput, CvTrackPoint
from tactiq.evaluation.gates import release_gates
from tactiq.evaluation.tracking import AnnotatedFrame, evaluate_calibration, evaluate_tracking


def test_identity_switches_are_not_hidden_by_detection_accuracy():
    frames = [
        AnnotatedFrame(frame_idx=i, timestamp_ms=i * 1000, objects=[
            {"object_id": "player", "object_class": "player", "team_id": "team_a", "bbox_xywh": (10, 10, 20, 40)}
        ]) for i in range(16)
    ]
    predictions = [
        CvTrackPoint(track_id="a" if i < 8 else "b", object_class="player", team_id="team_a", confidence=1,
                     frame_idx=i, timestamp_ms=i * 1000, bbox_xywh=(10, 10, 20, 40))
        for i in range(16)
    ]
    score = evaluate_tracking(frames, predictions)
    assert score["precision"] == score["recall"] == 1
    assert score["identity_switches"] == 1
    assert score["player_id_consistency"] == 0.5
    assert score["idf1"] == 0.5
    assert score["ball_recovery_max_ms"] is None


def test_false_positives_on_empty_annotated_frames_are_counted():
    score = evaluate_tracking(
        [AnnotatedFrame(frame_idx=0, timestamp_ms=0, objects=[])],
        [CvTrackPoint(track_id="false", object_class="player", confidence=1, frame_idx=0, timestamp_ms=0,
                      bbox_xywh=(0, 0, 10, 10))]
    )
    assert score["false_positives"] == 1


def test_calibration_is_scored_against_held_out_landmarks():
    calibration = fit_calibration(CalibrationInput(
        image_points=[(0, 0), (200, 0), (200, 100), (0, 100)],
        pitch_points=[(0, 0), (100, 0), (100, 100), (0, 100)]
    ), 200, 100)
    score = evaluate_calibration(calibration, [
        {"image_point": [100, 50], "pitch_point": [50, 50]},
        {"image_point": [50, 25], "pitch_point": [25, 25]},
    ])
    assert score["coverage"] == 1
    assert score["rmse_m"] < 1e-6


def test_missing_or_synthetic_evidence_never_passes_production_gate():
    assert not release_gates({})["production_ready"]
    fake = release_gates({"dataset": {"domain": "synthetic", "reviewer": "test", "license_reference": "generated"}})
    assert not fake["checks"][0]["passed"]
    assert not fake["production_ready"]


def test_a_later_occlusion_cycle_cannot_hide_a_missed_ball_recovery():
    frames = [
        AnnotatedFrame(frame_idx=index, timestamp_ms=index * 500, objects=[{
            "object_id": "ball", "object_class": "ball", "bbox_xywh": (10, 10, 8, 8), "visible": visible,
        }])
        for index, visible in enumerate((True, False, True, False, True))
    ]
    predictions = [CvTrackPoint(track_id="ball_1", object_class="ball", confidence=1,
                                frame_idx=index, timestamp_ms=index * 500, bbox_xywh=(10, 10, 8, 8))
                   for index in (0, 4)]
    score = evaluate_tracking(frames, predictions)
    assert score["occlusion_cases"] == 2
    assert score["unrecovered_occlusions"] == 1
    assert score["ball_recovery_max_ms"] == 0


def test_invalid_annotation_timing_and_duplicate_identities_are_rejected():
    with pytest.raises(ValueError, match="timestamps"):
        evaluate_tracking([AnnotatedFrame(frame_idx=0, timestamp_ms=500, objects=[]),
                           AnnotatedFrame(frame_idx=1, timestamp_ms=250, objects=[])], [])
    with pytest.raises(ValueError, match="once"):
        AnnotatedFrame(frame_idx=0, timestamp_ms=0, objects=[
            {"object_id": "same", "object_class": "player", "bbox_xywh": (10, 10, 8, 8)},
            {"object_id": "same", "object_class": "player", "bbox_xywh": (20, 10, 8, 8)},
        ])
