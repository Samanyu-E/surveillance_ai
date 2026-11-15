from pathlib import Path

import cv2
import numpy as np
import pytest

from survai import calibrate
from survai.config import load_walkout_config
from survai.main import DEMO_CONFIG, main


def test_calibration_cli_saves_normalized_zones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = tmp_path / "backup.mp4"
    writer = cv2.VideoWriter(str(replay), cv2.VideoWriter_fourcc(*"mp4v"), 12, (320, 180))
    assert writer.isOpened()
    writer.write(np.zeros((180, 320, 3), dtype=np.uint8))
    writer.release()
    actions = iter([
        (ord("1"), []),
        (13, [(20, 30), (180, 150)]),
        (ord("2"), []),
        (13, [(200, 90), (240, 140)]),
        (ord("4"), []),
        (ord("s"), [(270, 0), (270, 179)]),
    ])
    callback = None

    def set_mouse_callback(window: str, handler: object) -> None:
        nonlocal callback
        callback = handler

    def wait_key(_: int) -> int:
        key, points = next(actions)
        assert callable(callback)
        for x, y in points:
            callback(cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
        return key

    def show_frame(window: str, frame: np.ndarray) -> None:
        assert frame.shape == (259, 320, 3)

    monkeypatch.setattr(calibrate.cv2, "setMouseCallback", set_mouse_callback)
    monkeypatch.setattr(calibrate.cv2, "waitKey", wait_key)
    monkeypatch.setattr(calibrate.cv2, "imshow", show_frame)
    monkeypatch.setattr(calibrate.cv2, "namedWindow", lambda *_: None)
    monkeypatch.setattr(calibrate.cv2, "destroyWindow", lambda *_: None)
    monkeypatch.setattr(calibrate.cv2, "getWindowProperty", lambda *_: 1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["survai", "--calibrate", "--source", str(replay)])
    assert main() == 0
    saved = load_walkout_config(tmp_path / "walkout.local.yaml")
    assert saved.dining_zone[0] == (20 / 319, 30 / 179)
    assert saved.pay_zone == [
        (200 / 319, 90 / 179), (240 / 319, 90 / 179),
        (240 / 319, 140 / 179), (200 / 319, 140 / 179),
    ]
    assert saved.exit_line == ((270 / 319, 0), (270 / 319, 1))
    assert saved.exit_band is None
    assert saved.min_dwell_seconds == load_walkout_config(DEMO_CONFIG).min_dwell_seconds
