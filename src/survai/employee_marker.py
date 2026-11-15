import cv2
import numpy as np

from .capture import Frame
from .tracks import Track


class PhoneMarker:
    """Find three similarly sized, vertically stacked screen bands within a person box."""

    def matches(self, hsv: Frame, track: Track) -> bool:
        frame_height, frame_width = hsv.shape[:2]
        left, top, right, bottom = track.box
        x1, x2 = (int(np.clip(x, 0, 1) * frame_width) for x in (left, right))
        y1, y2 = (int(np.clip(y, 0, 1) * frame_height) for y in (top, bottom))
        crop = hsv[y1:y2, x1:x2]
        if min(crop.shape[:2]) < 16:
            return False
        center = (x2 - x1) / 2

        def bands(low: int, high: int) -> list[tuple[int, int, int, int]]:
            mask = cv2.inRange(crop, (low, 100, 80), (high, 255, 255))
            if low == 0:
                mask |= cv2.inRange(crop, (170, 100, 80), (179, 255, 255))
            count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
            return [
                (int(x), int(y), int(w), int(h)) for x, y, w, h, area in stats[1:count]
                if w >= 7 and h >= 4 and 0.7 <= w / h <= 4.5
                and area >= 0.65 * w * h and abs(x + w / 2 - center) <= 0.4 * (x2 - x1)
            ]

        def follows(upper: tuple[int, ...], lower: tuple[int, ...]) -> bool:
            x, y, w, h = upper
            a, b, width, height = lower
            return (
                0.65 <= width / w <= 1.55 and 0.55 <= height / h <= 1.8
                and abs(x + w / 2 - a - width / 2) <= 0.35 * w
                and -0.3 * h <= b - y - h <= 0.65 * h
            )

        reds, greens, blues = bands(0, 12), bands(38, 90), bands(93, 138)
        return any(follows(red, green) and follows(green, blue)
                   for green in greens for red in reds for blue in blues)
