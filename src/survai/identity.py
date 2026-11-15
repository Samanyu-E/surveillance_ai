from collections import deque
from dataclasses import dataclass, field
from math import dist

import cv2
import lap
import numpy as np
from numpy.typing import NDArray

from .capture import Frame
from .tracks import Track
from .zones import Point

Box = tuple[float, float, float, float]
Appearance = NDArray[np.float32]
MIN_CONFIRMATION_FRAMES = 3
CONFIRMATION_GAP_SECONDS = 0.5


def center(box: Box) -> Point:
    left, top, right, bottom = box
    return (left + right) / 2, (top + bottom) / 2


def overlap(a: Box, b: Box) -> float:
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return width * height / area if area > 0 else 0


def duplicate_box(a: Box, b: Box) -> bool:
    if overlap(a, b) < 0.9:
        return False
    # Compare size as well as containment so a partly visible person can
    # still be detected in front of a larger box.
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return min(area_a, area_b) >= 0.85 * max(area_a, area_b)


def clothing_colors(frame: Frame, box: Box) -> Appearance | None:
    height, width = frame.shape[:2]
    left, top, right, bottom = box
    dx, dy = right - left, bottom - top
    # Sample the central torso and compare coarse colors without retaining images.
    crop = frame[
        max(0, round((top + 0.45 * dy) * height)):round((top + 0.8 * dy) * height),
        max(0, round((left + 0.22 * dx) * width)):round((right - 0.22 * dx) * width),
    ]
    if crop.size == 0:
        return None
    sample = cv2.resize(crop, (24, 24), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256])
    return histogram / histogram.sum()


@dataclass
class Identity:
    box: Box
    raw_id: int | None
    seen_at: float
    colors: deque[Appearance] = field(default_factory=lambda: deque(maxlen=4))
    last_color_at: float = float("-inf")
    consecutive_frames: int = 1
    initial_tracks: list[Track] = field(default_factory=list)
    velocity: Point = (0.0, 0.0)
    occluded: bool = False

    def predict(self, now: float) -> Point:
        x, y = center(self.box)
        dt = min(now - self.seen_at, 0.25)
        return x + self.velocity[0] * dt, y + self.velocity[1] * dt


class PersonIdentities:
    def __init__(self, lost_grace: float, distance: float, occlusion_seconds: float) -> None:
        self.lost_grace = lost_grace
        self.distance = distance
        self.occlusion_seconds = occlusion_seconds
        self.identities: dict[int, Identity] = {}
        self.tentative: dict[int, Identity] = {}
        self.hidden_ids: set[int] = set()
        self.confirmed_paths: dict[int, list[Track]] = {}
        self.next_id = 1

    def forget_tracker_ids(self) -> None:
        for identity in self.identities.values():
            identity.raw_id = None
        self.tentative.clear()
        self.confirmed_paths.clear()

    def _unique_tracks(self, tracks: list[Track], now: float) -> list[Track]:
        recent_ids = {
            identity.raw_id for identity in self.identities.values()
            if now - identity.seen_at < self.lost_grace
        }
        ranked = sorted(range(len(tracks)), key=lambda index: tracks[index].id not in recent_ids)
        kept: list[int] = []
        for index in ranked:
            if all(not duplicate_box(tracks[index].box, tracks[other].box) for other in kept):
                kept.append(index)
        return [tracks[index] for index in sorted(kept)]

    def update(self, frame: Frame, tracks: list[Track], now: float) -> list[Track]:
        tracks = self._unique_tracks(tracks, now)
        samples = [clothing_colors(frame, track.box) for track in tracks]
        for identity in self.identities.values():
            if now - identity.seen_at < self.lost_grace and not any(
                track.id == identity.raw_id for track in tracks
            ):
                identity.occluded |= any(overlap(identity.box, track.box) >= 0.25 for track in tracks)

        eligible = [
            (key, identity) for key, identity in self.identities.items()
            if now - identity.seen_at < (
                max(self.lost_grace, self.occlusion_seconds) if identity.occluded else self.lost_grace
            )
        ]
        self.identities = dict(eligible)
        matched = [-1] * len(tracks)
        if eligible and tracks:
            costs = np.array([
                [self._cost(identity, track, colors, now) for track, colors in zip(tracks, samples, strict=True)]
                for _, identity in eligible
            ])
            _, _, matched = lap.lapjv(costs, extend_cost=True, cost_limit=1.0)

        visible = []
        self.confirmed_paths.clear()
        next_tentative = {}
        for index, (track, colors) in enumerate(zip(tracks, samples, strict=True)):
            crowded = any(
                index != other and overlap(track.box, candidate.box) >= 0.25
                for other, candidate in enumerate(tracks)
            )
            match = matched[index]
            if match < 0:
                identity = self.tentative.get(track.id)
                if (identity is None or now - identity.seen_at > CONFIRMATION_GAP_SECONDS
                        or self._cost(identity, track, None, now) >= 1):
                    identity = Identity(track.box, track.id, now)
                else:
                    identity.consecutive_frames += 1
                self._refresh(identity, track, None if crowded else colors, now)
                identity.initial_tracks.append(track)
                if identity.consecutive_frames < MIN_CONFIRMATION_FRAMES:
                    next_tentative[track.id] = identity
                    continue
                key = self.next_id
                self.next_id += 1
                self.confirmed_paths[key] = identity.initial_tracks
                identity.initial_tracks = []
                self.identities[key] = identity
            else:
                key, identity = eligible[match]
                self._refresh(identity, track, None if crowded else colors, now)
            visible.append(Track(key, track.kind, track.box))

        self.tentative = next_tentative
        visible_ids = {track.id for track in visible}
        # A raw tracker ID can also jump to the person in front.
        for key, identity in self.identities.items():
            if key not in visible_ids and now - identity.seen_at < self.lost_grace:
                identity.occluded |= any(overlap(identity.box, track.box) >= 0.25 for track in tracks)
        self.hidden_ids = {key for key, identity in self.identities.items() if identity.occluded}
        return visible

    def _cost(
        self, identity: Identity, track: Track, colors: Appearance | None, now: float,
    ) -> float:
        point = center(track.box)
        same_raw = identity.raw_id == track.id
        span = max(identity.box[2] - identity.box[0], track.box[2] - track.box[0])
        radius = max(0.12, self.distance, min(span * 0.75, 0.25))
        if identity.occluded:
            radius = max(radius * 1.6, 0.19)
        elif same_raw:
            radius *= 1.5
        proximity = min(dist(point, identity.predict(now)), dist(point, center(identity.box)) * 1.1)
        intersection = overlap(identity.box, track.box)
        if proximity > radius and intersection < 0.35:
            return 2.0
        difference = min(
            (cv2.compareHist(sample, colors, cv2.HISTCMP_BHATTACHARYYA) for sample in identity.colors),
            default=0.0,
        ) if colors is not None else 0.0
        continuity = 0.18 if same_raw else 0
        recovery = 0.10 if identity.occluded else 0
        geometry = min(proximity / radius, 1.15 - intersection)
        return 0.55 * geometry + 0.68 * difference + recovery - continuity

    @staticmethod
    def _refresh(identity: Identity, track: Track, colors: Appearance | None, now: float) -> None:
        dt = now - identity.seen_at
        if 0 < dt <= 0.3 and not identity.occluded:
            old_x, old_y = center(identity.box)
            x, y = center(track.box)
            vx, vy = identity.velocity
            identity.velocity = (0.5 * vx + 0.5 * (x - old_x) / dt, 0.5 * vy + 0.5 * (y - old_y) / dt)
        else:
            identity.velocity = (0.0, 0.0)
        if colors is not None and now - identity.last_color_at >= 0.5:
            identity.colors.append(colors)
            identity.last_color_at = now
        identity.box, identity.raw_id, identity.seen_at = track.box, track.id, now
        identity.occluded = False
