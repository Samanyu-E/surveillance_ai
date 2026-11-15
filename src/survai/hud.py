from collections.abc import Set
from datetime import datetime
from math import hypot

import cv2
import numpy as np

from .capture import Frame
from .detectors.walkout import VisitState, WalkoutDetector
from .tracks import Track
from .zones import Point


def annotate_walkout(
    frame: Frame, tracks: list[Track], detector: WalkoutDetector | None, fps: float, flash: bool,
    *, displayed_at: datetime | None = None, global_labels: bool = False,
    door_line: tuple[Point, Point] | None = None, employees: Set[int] = frozenset(),
    customer_labels: bool = False, door_anchor: str = "center", alert_title: str = "POSSIBLE WALK-OUT",
) -> Frame:
    height, width = frame.shape[:2]
    scale = max(0.55, width / 960)
    thickness = max(1, round(2 * scale))
    config = detector.config if detector is not None else None
    background = (24, 19, 14)
    mint = (193, 244, 129)
    ice = (255, 211, 144)
    amber = (129, 199, 255)

    def pixel(point: Point) -> tuple[int, int]:
        return round(point[0] * (width - 1)), round(point[1] * (height - 1))

    def label(text: str, at: tuple[int, int], color: tuple[int, int, int]) -> None:
        x, y = at
        (length, text_height), baseline = cv2.getTextSize(text, 0, scale, thickness)
        x = max(0, min(x, width - length - 8))
        y = max(text_height + 4, min(y, height - baseline - 4))
        top = y - text_height - 5
        cv2.rectangle(frame, (x, top), (x + length + 9, y + baseline + 2), background, -1)
        cv2.line(frame, (x, top), (x, y + baseline + 2), color, thickness, cv2.LINE_AA)
        cv2.putText(frame, text, (x + 5, y), 0, scale, color, thickness, cv2.LINE_AA)

    if config is not None:
        for name, polygon, color in (
            ("DINING", config.dining_zone, ice),
            ("COUNTER", config.pay_zone, mint),
            ("EXIT", config.exit_band, amber),
        ):
            if polygon is not None:
                pixels = np.array([pixel(point) for point in polygon], np.int32)
                cv2.polylines(frame, [pixels], True, background, thickness + 2, cv2.LINE_AA)
                cv2.polylines(frame, [pixels], True, color, thickness, cv2.LINE_AA)
                x, y = pixel(polygon[0])
                label(name, (x + 5, y + round(25 * scale)), color)
        if config.exit_line is not None:
            a, b = map(pixel, config.exit_line)
            cv2.line(frame, a, b, amber, thickness, cv2.LINE_AA)
            label("EXIT", a, amber)

    if door_line is not None:
        a, b = map(pixel, door_line)
        cv2.line(frame, a, b, background, thickness + 3, cv2.LINE_AA)
        cv2.line(frame, a, b, mint, thickness, cv2.LINE_AA)
        middle = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = max(1, hypot(dx, dy))
        tip = (middle[0] - round(dy / length * 38 * scale), middle[1] + round(dx / length * 38 * scale))
        cv2.arrowedLine(frame, middle, tip, mint, thickness, cv2.LINE_AA)
        label("DOOR IN", tip, mint)

    for track in tracks:
        visit = detector.visits.get(track.id) if detector is not None else None
        if detector is not None and visit is None:
            continue
        left, top, right, bottom = track.box
        x, y = pixel((left, top))
        right_x, bottom_y = pixel((right, bottom))
        palette = (ice, mint, (248, 162, 219), (155, 207, 255), (249, 208, 174))
        color = palette[(track.id - 1) % len(palette)] if global_labels else (
            mint if visit is not None and visit.state == VisitState.PAID else ice
        )
        arm = max(1, min((right_x - x) // 3, (bottom_y - y) // 3, round(26 * scale)))
        for corner_x, direction_x in ((x, 1), (right_x, -1)):
            for corner_y, direction_y in ((y, 1), (bottom_y, -1)):
                bracket = np.array([
                    (corner_x + direction_x * arm, corner_y),
                    (corner_x, corner_y),
                    (corner_x, corner_y + direction_y * arm),
                ], np.int32)
                cv2.polylines(frame, [bracket], False, background, thickness + 3, cv2.LINE_AA)
                cv2.polylines(frame, [bracket], False, color, thickness + 1, cv2.LINE_AA)
        label_prefix = "Customer " if customer_labels else ("Guest " if global_labels else "#")
        is_employee = track.id in employees or (visit is not None and visit.state == VisitState.EMPLOYEE)
        prefix = "Employee " if is_employee else label_prefix
        if is_employee or visit is None or config is None:
            if door_line is not None:
                point = track.point if door_anchor == "bottom_center" else ((left + right) / 2, (top + bottom) / 2)
                cv2.circle(frame, pixel(point), max(2, round(4 * scale)), color, -1, cv2.LINE_AA)
            label(f"{prefix}{track.id}", (x, y - 6), color)
            continue
        anchor = pixel(visit.point)
        cv2.circle(frame, anchor, max(3, round(6 * scale)), background, -1, cv2.LINE_AA)
        cv2.circle(frame, anchor, max(2, round(3 * scale)), color, -1, cv2.LINE_AA)
        label(f"{prefix}{track.id} {visit.state.value}", (x, y - 6), color)
        progress = min(1.0, visit.dining_seconds / config.min_dwell_seconds)
        bar_y = min(bottom_y - 2, height - thickness - 1)
        cv2.line(frame, (x, bar_y), (right_x, bar_y), background, 3 * thickness)
        if progress:
            cv2.line(frame, (x, bar_y), (round(x + (right_x - x) * progress), bar_y), color, thickness + 1)

    displayed_at = displayed_at or datetime.now().astimezone()
    stamp = displayed_at.strftime("%Y-%m-%d  %H:%M:%S")
    telemetry = f"{fps:.1f} FPS   /   {stamp}"
    label(telemetry, (round(12 * scale), height - round(16 * scale)), mint)
    if flash and config is not None:
        warning = (168, 121, 255)
        cv2.rectangle(frame, (0, 0), (width - 1, height - 1), warning, 2 * thickness)
        title = alert_title
        length = cv2.getTextSize(title, 0, scale, thickness)[0][0]
        label(title, ((width - length) // 2, round(36 * scale)), warning)
    return frame
