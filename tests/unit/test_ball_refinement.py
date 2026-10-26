import numpy as np

from tactiq.cv.detection import Detection, OnnxDetector
from tactiq.cv.tracking import MultiObjectTracker, SceneContinuity, TeamClassifier


def test_local_ball_confirmation_rejects_a_weak_global_boot_candidate():
    detector = object.__new__(OnnxDetector)
    detector.reset_tracking()
    detector._infer = lambda frame: [Detection("ball", .21, (96, 96, 8, 8))] if frame.shape[1] == 400 else []
    assert detector.detect(np.zeros((200, 400, 3), dtype=np.uint8)) == []


def test_local_ball_coordinates_are_mapped_back_to_the_source_frame():
    detector = object.__new__(OnnxDetector)
    detector.reset_tracking()

    def infer(frame):
        return [Detection("ball", .42, (96, 96, 8, 8))] if frame.shape[1] == 400 else [
            Detection("ball", .7, (86, 86, 8, 8))
        ]
    detector._infer = infer
    result = detector.detect(np.zeros((200, 400, 3), dtype=np.uint8))
    assert len(result) == 1 and result[0].bbox == (96, 96, 8, 8)
    assert result[0].confidence == .7


def test_observed_ball_uses_the_measured_box_instead_of_lagging_filtered_position():
    tracker = MultiObjectTracker()
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    teams = TeamClassifier()
    original = tracker.update([Detection("ball", .8, (50, 80, 8, 8))], frame, 0, teams)[0]
    moved = tracker.update([Detection("ball", .8, (80, 80, 8, 8))], frame, 125, teams)[0]
    assert moved.track_id == original.track_id
    assert moved.bbox == (80, 80, 8, 8)


def test_new_observed_ball_does_not_leave_an_older_ghost_candidate_to_reappear():
    tracker = MultiObjectTracker()
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    teams = TeamClassifier()
    first = tracker.update([Detection("ball", .95, (10, 20, 8, 8))], frame, 0, teams)[0]
    second = tracker.update([Detection("ball", .7, (300, 150, 8, 8))], frame, 125, teams)[0]
    assert second.track_id != first.track_id
    assert len(tracker.update([], frame, 250, teams)) == 1
    assert next(iter(tracker.tracks)) == second.track_id


def test_camera_cut_resets_identity_without_reusing_the_old_track_id():
    continuity = SceneContinuity()
    green = np.full((200, 400, 3), (20, 140, 20), dtype=np.uint8)
    blue = np.full_like(green, (200, 30, 30))
    assert not continuity.update(green)
    assert not continuity.update(green)
    assert continuity.update(blue)
    tracker = MultiObjectTracker()
    first = tracker.update([Detection("ball", .8, (20, 20, 8, 8))], green, 0, TeamClassifier())[0]
    tracker.reset()
    second = tracker.update([Detection("ball", .8, (20, 20, 8, 8))], blue, 125, TeamClassifier())[0]
    assert first.track_id != second.track_id
