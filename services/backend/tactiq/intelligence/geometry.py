from typing import Any


def has_pitch_geometry(frame: dict[str, Any]) -> bool:
    return bool(frame.get("calibrated") or (
        frame.get("geometry_source") == "simulation" and frame.get("detection_source") == "synthetic"
    ))
