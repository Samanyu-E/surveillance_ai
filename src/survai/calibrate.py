import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from .capture import Capture, Frame
from .config import WalkoutConfig, save_config
from .zones import Point

LOG = logging.getLogger(__name__)
Zone = Literal["dining_zone", "pay_zone", "exit_band", "exit_line"]
ZONES: dict[int, Zone] = {
    ord("1"): "dining_zone",
    ord("2"): "pay_zone",
    ord("3"): "exit_band",
    ord("4"): "exit_line",
}


@dataclass
class Calibration:
    config: WalkoutConfig
    zone: Zone = "dining_zone"
    points: list[Point] = field(default_factory=list)
    message: str = ""

    def click(self, x: int, y: int, width: int, height: int) -> None:
        point = (x / max(1, width - 1), y / max(1, height - 1))
        if not all(0 <= value <= 1 for value in point):
            return
        if len(self.points) == 2:
            self.message = "Press Enter to apply or right-click to undo."
            return
        if point not in self.points:
            self.points.append(point)
            self.message = ""

    def apply(self) -> None:
        if len(self.points) != 2:
            raise ValueError("Choose two opposite corners or two line endpoints")
        settings = self.config.model_dump()
        if self.zone == "exit_line":
            settings[self.zone] = self.points.copy()
            settings["exit_band"] = None
        else:
            (ax, ay), (bx, by) = self.points
            left, right = sorted((ax, bx))
            top, bottom = sorted((ay, by))
            settings[self.zone] = [(left, top), (right, top), (right, bottom), (left, bottom)]
            if self.zone == "exit_band":
                settings["exit_line"] = None
        self.config = WalkoutConfig.model_validate(settings)
        self.points.clear()
        self.message = "Zone applied. Choose another zone or press S to save."


def draw_calibration(frame: Frame, calibration: Calibration) -> Frame:
    height, width = frame.shape[:2]
    draft = calibration.points
    if calibration.zone != "exit_line" and len(draft) == 2:
        (ax, ay), (bx, by) = draft
        draft = [(ax, ay), (bx, ay), (bx, by), (ax, by)]
    for name, points, color in (
        ("DINING", calibration.config.dining_zone, (255, 205, 90)),
        ("COUNTER", calibration.config.pay_zone, (110, 235, 130)),
        ("EXIT", calibration.config.exit_band or calibration.config.exit_line, (100, 180, 255)),
        ("", draft, (255, 255, 255)),
    ):
        if points:
            pixels = np.array([(round(x * (width - 1)), round(y * (height - 1))) for x, y in points], np.int32)
            cv2.polylines(frame, [pixels], bool(name) and len(points) > 2, color, 2)
            if name:
                x, y = pixels[0]
                x = min(x + 5, width - cv2.getTextSize(name, 0, 0.55, 2)[0][0] - 5)
                cv2.putText(frame, name, (max(0, x), min(height - 5, y + 22)), 0, 0.55, color, 2, cv2.LINE_AA)
            else:
                for point in pixels:
                    cv2.circle(frame, point, 4, color, -1)

    zone = "COUNTER" if calibration.zone == "pay_zone" else calibration.zone.replace("_", " ").upper()
    instructions = (
        "Click 2 endpoints. Right-edge exit: click top, then bottom."
        if calibration.zone == "exit_line" else "Click two opposite rectangle corners."
    )
    frame = cv2.copyMakeBorder(frame, 0, 79, 0, 0, cv2.BORDER_CONSTANT, value=(20, 20, 20))
    for baseline, text in (
        (20, "1 Dining   2 Counter   3 Exit band   4 Exit line"),
        (43, f"{zone} | {calibration.message or instructions}"),
        (66, "Right-click undo   Enter apply   S save   Q quit"),
    ):
        scale = min(0.5, (width - 15) / cv2.getTextSize(text, 0, 1, 1)[0][0])
        cv2.putText(frame, text, (7, height + baseline), 0, scale, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def run_calibration(
    config: WalkoutConfig, source: str, destination: Path, *, mirror: bool = True,
    save: Callable[[WalkoutConfig], None] | None = None,
) -> int:
    window = "Walk-out calibration"
    capture = Capture(source, native_resolution=True)
    window_open = False
    try:
        frame = capture.first_frame()
        height, width = frame.shape[:2]
        scale = min(1.0, 960 / width, 540 / height)
        display_width, display_height = max(1, round(width * scale)), max(1, round(height * scale))
        calibration = Calibration(config)

        def mouse(event: int, x: int, y: int, _flags: int, _data: object) -> None:
            if event == cv2.EVENT_LBUTTONDOWN:
                calibration.click(x, y, display_width, display_height)
            elif event == cv2.EVENT_RBUTTONDOWN and calibration.points:
                calibration.points.pop()
                calibration.message = ""

        cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
        window_open = True
        cv2.setMouseCallback(window, mouse)
        while True:
            mirrored = cv2.flip(frame, 1) if mirror else frame.copy()
            if display_width != width:
                mirrored = cv2.resize(mirrored, (display_width, display_height), interpolation=cv2.INTER_AREA)
            cv2.imshow(window, draw_calibration(mirrored, calibration))
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) == 0:
                return 0
            if key in ZONES:
                if calibration.points:
                    calibration.message = "Apply this draft or undo its points before switching."
                else:
                    calibration.zone = ZONES[key]
                    calibration.message = ""
            if key in (10, 13, ord("s")):
                try:
                    if calibration.points or key != ord("s"):
                        calibration.apply()
                    if key == ord("s"):
                        if save is None:
                            save_config(destination, calibration.config)
                        else:
                            save(calibration.config)
                        LOG.info("Saved calibration to %s", destination.resolve())
                        return 0
                except ValueError:
                    calibration.message = "Use 2 opposite corners with area or 2 distinct line endpoints."
            if capture.live:
                next_frame = capture.read()
                if next_frame is None:
                    raise RuntimeError("Camera stopped delivering frames")
                frame = next_frame
    finally:
        capture.close()
        if window_open:
            cv2.destroyWindow(window)
