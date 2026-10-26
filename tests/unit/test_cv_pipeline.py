import cv2
import numpy as np
import pytest

from tactiq.cv.calibration import fit_calibration, project_point
from tactiq.cv.detection import Detection, MotionDetector, configured_detector, CvUnavailableError
from tactiq.cv.events import tracks_to_events
from tactiq.cv.ingest import extract_cv_tracks_from_video_bytes
from tactiq.cv.tracking import MultiObjectTracker, TeamClassifier
from tactiq.cv.types import CalibrationInput, CameraCalibration, CvIngestResult, CvOptions, CvTrackPoint


def test_calibration_maps_holdout_point_and_rejects_collinear_points():
    points = CalibrationInput(
        image_points=[(20, 10), (180, 10), (180, 90), (20, 90)],
        pitch_points=[(0, 0), (100, 0), (100, 100), (0, 100)],
    )
    calibration = fit_calibration(points, 200, 100)
    assert calibration.status == "calibrated"
    assert calibration.reprojection_error_m < 0.001
    assert project_point(calibration, 100, 50) == pytest.approx((50, 50))
    assert project_point(CameraCalibration(method="unavailable", confidence=0), 100, 50) is None
    with pytest.raises(ValueError, match="collinear"):
        fit_calibration(CalibrationInput(
            image_points=[(0, 0), (1, 1), (2, 2), (3, 3)],
            pitch_points=points.pitch_points,
        ), 200, 100)


def test_tracking_recovers_ids_and_expires_occluded_ball():
    tracker = MultiObjectTracker(max_age_ms=1500)
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    teams = TeamClassifier()
    first = tracker.update([Detection("ball", 0.9, (20, 40, 8, 8))], frame, 0, teams)[0]
    next_track = tracker.update([Detection("ball", 0.9, (30, 40, 8, 8))], frame, 125, teams)[0]
    assert next_track.track_id == first.track_id
    predicted = tracker.update([], frame, 250, teams)[0]
    assert predicted.inferred
    assert predicted.last_observed_ms == 125
    assert predicted.state[0] > next_track.bbox[0]
    assert tracker.update([], frame, 1750, teams) == []


def test_two_team_colors_keep_player_ids_through_crossing():
    tracker = MultiObjectTracker()
    teams = TeamClassifier({"team_a": (0, 0, 255), "team_b": (255, 0, 0)})
    identities = {}
    for index, positions in enumerate([(40, 100), (55, 85), (70, 70), (85, 55), (100, 40)]):
        frame = np.zeros((200, 400, 3), dtype=np.uint8)
        detections = []
        for x, y, bgr in [(positions[0], 20, (255, 0, 0)), (positions[1], 80, (0, 0, 255))]:
            cv2.rectangle(frame, (x, y), (x + 20, y + 50), bgr, -1)
            detections.append(Detection("player", 0.9, (x, y, 20, 50)))
        tracks = tracker.update(detections[::-1] if index % 2 else detections, frame, index * 125, teams)
        current = {track.team[0]: track.track_id for track in tracks if not track.inferred}
        if index == 0:
            identities = current
        assert current == identities


def test_missing_model_is_an_explicit_configuration_error(monkeypatch):
    monkeypatch.delenv("TACTIQ_CV_MODEL_PATH", raising=False)
    monkeypatch.setenv("TACTIQ_CV_DETECTOR", "onnx")
    with pytest.raises(CvUnavailableError, match="TACTIQ_CV_MODEL_PATH"):
        configured_detector()


def test_video_ingest_never_claims_uncalibrated_metric_positions(tmp_path):
    path = tmp_path / "generated.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 100))
    for index in range(20):
        frame = np.zeros((100, 160, 3), dtype=np.uint8)
        cv2.rectangle(frame, (10 + index, 20), (20 + index, 60), (255, 0, 0), -1)
        writer.write(frame)
    writer.release()
    result = extract_cv_tracks_from_video_bytes(
        path.read_bytes(), options=CvOptions(max_duration_seconds=1), detector=MotionDetector()
    )
    assert result.detection_source == "heuristic"
    assert result.truncated
    assert result.calibration.homography is None
    assert all(point.pitch_position is None and point.speed_kmh is None for point in result.tracks)
    assert result.sampled_timestamps_ms == sorted(result.sampled_timestamps_ms)


def test_track_adapter_clears_missing_observations_and_preserves_provenance():
    point = CvTrackPoint(
        track_id="ball_1", object_class="ball", confidence=0.8,
        frame_idx=0, timestamp_ms=0, bbox_xywh=(0, 0, 8, 8),
        image_position=(50, 50),
    )
    result = CvIngestResult(
        extractor="test-model", detection_source="model", model_id="test",
        fps=25, width=100, height=100, frame_count=25, sampled_frames=2,
        tracks=[point], sampled_timestamps_ms=[0, 125],
        calibration=CameraCalibration(method="unavailable", confidence=0),
        notes="test",
    )
    events = tracks_to_events(result, "test")
    assert len(events) == 2
    assert not events[0].synthetic
    assert events[0].position is None
    assert events[1].qualifiers["cv_frame"]["ball"] is None
    assert events[0].qualifiers["cv_frame"]["model_id"] == "test"


def test_new_camera_survey_does_not_turn_a_static_ball_into_a_speed_burst(tmp_path):
    class StaticBallDetector:
        name = "fixed-observation-fixture"
        source = "model"
        model_id = "test-only"

        def detect(self, frame):
            return [Detection("ball", 0.95, (60, 40, 8, 8))]

    path = tmp_path / "static.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 100))
    for _ in range(12):
        writer.write(np.zeros((100, 160, 3), dtype=np.uint8))
    writer.release()
    image_points = [(0, 0), (160, 0), (160, 100), (0, 100)]
    result = extract_cv_tracks_from_video_bytes(path.read_bytes(), options=CvOptions(
        sample_fps=10,
        calibrations=[
            CalibrationInput(image_points=image_points, pitch_points=[(0, 0), (100, 0), (100, 100), (0, 100)]),
            CalibrationInput(timestamp_ms=500, image_points=image_points,
                             pitch_points=[(5, 0), (95, 0), (95, 100), (5, 100)]),
        ],
    ), detector=StaticBallDetector())
    resurveyed = next(point for point in result.tracks if point.timestamp_ms == 500)
    assert resurveyed.speed_kmh is None
    after = next(point for point in result.tracks if point.timestamp_ms == 600)
    assert after.speed_kmh == 0
    assert [item.revision for item in result.calibration_history][5:7] == [2, 2]


def test_tracking_gap_does_not_imply_a_new_recovery():
    calibration = CameraCalibration(method="fixture", confidence=1, status="calibrated")
    points = []
    for index, timestamp in enumerate((0, 250, 6000, 6250)):
        x = 20 if timestamp < 1000 else 80
        owner = "player_1" if x == 20 else "player_2"
        team = "team_a" if x == 20 else "team_b"
        for track_id, kind in ((owner, "player"), ("ball_1", "ball")):
            points.append(CvTrackPoint(
                track_id=track_id, object_class=kind, confidence=1,
                team_id=team if kind == "player" else None, team_confidence=1,
                frame_idx=index, timestamp_ms=timestamp, bbox_xywh=(x, 40, 8, 8),
                pitch_position=(x, 50), image_position=(x, 50),
            ))
    result = CvIngestResult(
        extractor="fixture", fps=4, width=100, height=100, frame_count=4, sampled_frames=4,
        tracks=points, calibration=calibration, sampled_timestamps_ms=[0, 250, 6000, 6250],
        detection_source="model", notes="Generated disconnected possession fixtures",
    )
    assert {event.type for event in tracks_to_events(result, "test")} == {"tracking_frame"}
