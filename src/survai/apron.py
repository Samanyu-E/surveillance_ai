import cv2
import numpy as np

from .capture import Frame
from .tracks import Track
from .zones import Point


class ApronColor:
    """Match a sampled uniform color within the tracked person's torso."""

    def __init__(self, hex_color: str) -> None:
        rgb = [int(hex_color[start:start + 2], 16) for start in (1, 3, 5)]
        self.lab = cv2.cvtColor(np.uint8([[rgb]]), cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)

    def matches(self, frame: Frame, track: Track) -> bool | None:
        height, width = frame.shape[:2]
        left, top, right, bottom = track.box
        w, h = right - left, bottom - top

        def region(start: Point, end: Point) -> Frame:
            x1, x2 = (round(np.clip(left + x * w, 0, 1) * width) for x in (start[0], end[0]))
            y1, y2 = (round(np.clip(top + y * h, 0, 1) * height) for y in (start[1], end[1]))
            return frame[y1:y2, x1:x2]

        bib = region((0.27, 0.33), (0.73, 0.57))
        waist = region((0.27, 0.57), (0.73, 0.8))
        if min(*bib.shape[:2], *waist.shape[:2]) < 8:
            return None

        def coverage(crop: Frame) -> float:
            pixels = cv2.resize(crop, (20, 20), interpolation=cv2.INTER_AREA)
            lab = cv2.cvtColor(pixels, cv2.COLOR_BGR2LAB).astype(np.float32)
            return float(np.mean(np.linalg.norm(lab - self.lab, axis=-1) <= 32))

        return coverage(bib) >= 0.42 and coverage(waist) >= 0.55


class EmployeeLabels:
    def __init__(self, *, sticky: bool = False) -> None:
        self.sticky = sticky
        self.scores: dict[int, tuple[int, float]] = {}
        self.employees: set[int] = set()

    def update(self, observations: dict[int, bool], now: float) -> None:
        self.scores = {guest: score for guest, score in self.scores.items() if now - score[1] <= 5}
        self.employees.intersection_update(self.scores)
        for guest, matched in observations.items():
            score, _ = self.scores.get(guest, (0, now))
            score = min(5, score + 1) if matched else max(0, score - 1)
            self.scores[guest] = score, now
            if score >= 3:
                self.employees.add(guest)
            elif score == 0 and not self.sticky:
                self.employees.discard(guest)
