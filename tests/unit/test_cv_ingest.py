import numpy as np

from tactiq.cv.ingest import classify_team_from_patch, map_to_pitch_percent


def test_classify_team_from_patch_blue_maps_team_a() -> None:
    patch = np.zeros((12, 12, 3), dtype=np.uint8)
    patch[:, :, 0] = 210  # blue channel
    patch[:, :, 2] = 25
    assert classify_team_from_patch(patch) == "team_a"


def test_classify_team_from_patch_red_maps_team_b() -> None:
    patch = np.zeros((12, 12, 3), dtype=np.uint8)
    patch[:, :, 2] = 225  # red channel
    patch[:, :, 0] = 35
    assert classify_team_from_patch(patch) == "team_b"


def test_classify_team_from_patch_low_saturation_unknown() -> None:
    patch = np.full((12, 12, 3), 120, dtype=np.uint8)
    assert classify_team_from_patch(patch) == "unknown"


def test_map_to_pitch_percent_clamps_bounds() -> None:
    x, y = map_to_pitch_percent(250.0, -10.0, width=200, height=100)
    assert x == 100.0
    assert y == 0.0
