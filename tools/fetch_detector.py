"""Build-time-only, digest-pinned upstream detector and redistribution license."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen


ARTIFACTS = (
    (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
        "yolox_tiny.onnx",
        30 * 1024 * 1024,
    ),
    (
        "https://raw.githubusercontent.com/Megvii-BaseDetection/YOLOX/0.1.1rc0/LICENSE",
        "577c03d505ec80f667ebf96ebd0cc4f6825c817ca1088ee348c48aaabd51bd92",
        "YOLOX-LICENSE.txt",
        64 * 1024,
    ),
)


def fetch(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for url, expected, name, limit in ARTIFACTS:
        with urlopen(url, timeout=120) as response:
            content = response.read(limit + 1)
        if len(content) > limit or hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Upstream artifact size or digest mismatch: {name}")
        (destination / name).write_bytes(content)
    (destination / "NOTICE.txt").write_text(
        "YOLOX-tiny ONNX, official Megvii-BaseDetection/YOLOX release 0.1.1rc0.\n"
        "Upstream: https://github.com/Megvii-BaseDetection/YOLOX\n"
        "Redistributed unmodified with the upstream Apache-2.0 license.\n"
        "General-purpose COCO detector; not independently validated football tracking.\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    fetch(Path("/opt/tactiq/models"))
