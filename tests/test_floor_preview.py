from pathlib import Path
from time import sleep
from unittest.mock import patch

import cv2
import numpy as np
import yaml

from survai.config import MultiCameraConfig
from survai.floor_calibration import floor_tool


def test_live_calibration_refreshes_both_cameras_and_saves_landmarks(tmp_path: Path) -> None:
    captures = []
    displayed = []
    callbacks = []

    class FakeCamera:
        live = True

        def __init__(self, source: str, *, native_resolution: bool) -> None:
            self.delay = 0.005 if source == "0" else 0.015
            self.reads = 0
            self.closed = False
            captures.append(self)

        def read(self) -> np.ndarray:
            sleep(self.delay)
            self.reads += 1
            brightness = 5 if self.reads <= 4 else 140
            return np.full((80, 160, 3), brightness, np.uint8)

        def close(self) -> None:
            self.closed = True

    def show(_window: str, frame: np.ndarray) -> None:
        displayed.append((frame[180, 240, 0], frame[180, 720, 0]))

    clicked = False

    def wait(_delay: int) -> int:
        nonlocal clicked
        if min(displayed[-1]) == 140 and not clicked:
            clicked = True
            for x, y in ((50, 110), (530, 110), (420, 110), (900, 110),
                         (420, 275), (900, 275), (50, 275), (530, 275)):
                callbacks[0](cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
            return ord("s")
        sleep(0.02)
        return ord("q") if len(displayed) >= 75 else 255

    config = MultiCameraConfig(cameras=[
        {"id": "mac", "name": "MacBook", "source": 0},
        {"id": "phone", "name": "iPhone", "source": 1},
    ])
    path = tmp_path / "layout.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(exclude_none=True)))
    with (
        patch("survai.floor_calibration.Capture", FakeCamera),
        patch("survai.floor_calibration.CAMERA_WARMUP_SECONDS", 0.03),
        patch("survai.floor_calibration.cv2.namedWindow"),
        patch("survai.floor_calibration.cv2.setMouseCallback", side_effect=lambda _, callback: callbacks.append(callback)),
        patch("survai.floor_calibration.cv2.imshow", side_effect=show),
        patch("survai.floor_calibration.cv2.waitKey", side_effect=wait),
        patch("survai.floor_calibration.cv2.getWindowProperty", return_value=1),
        patch("survai.floor_calibration.cv2.destroyWindow"),
    ):
        assert floor_tool(config, path, check=False) == 0

    assert min(displayed[0]) < 140
    assert displayed[-1] == (140, 140)
    assert all(camera.closed and camera.reads >= 5 for camera in captures)
    landmarks = yaml.safe_load(path.read_text())["calibration"]["landmarks"]
    assert len(landmarks) == 4
    assert all(landmark["mac"] == landmark["phone"] for landmark in landmarks)
