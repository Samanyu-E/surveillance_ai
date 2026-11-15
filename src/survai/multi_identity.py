from dataclasses import dataclass, field
from math import dist

import cv2
import lap
import numpy as np
from numpy.typing import NDArray

from .capture import Frame
from .config import IdentityConfig
from .identity import overlap
from .tracks import Track
from .zones import Point

Descriptor = NDArray[np.float32]


def describe(frame: Frame, track: Track, others: list[Track], config: IdentityConfig) -> Descriptor | None:
    height, width = frame.shape[:2]
    left, top, right, bottom = track.box
    if (bottom - top) * height < config.min_box_height_px or min(left, top, 1 - right, 1 - bottom) < 0.01:
        return None
    area = (right - left) * (bottom - top)
    for other in others:
        if other.id == track.id:
            continue
        x1, y1, x2, y2 = other.box
        other_area = (x2 - x1) * (y2 - y1)
        intersection = overlap(track.box, other.box) * min(area, other_area)
        if intersection / (area + other_area - intersection) > config.max_overlap_iou:
            return None
    margin = (right - left) * 0.18
    crop = frame[round(top * height):round(bottom * height),
                 round((left + margin) * width):round((right - margin) * width)]
    if min(crop.shape[:2]) < 8:
        return None
    sampled = cv2.resize(crop, (48, 72), interpolation=cv2.INTER_AREA)
    light = float(sampled.mean())
    if not 30 <= light <= 230:
        return None
    hsv = cv2.cvtColor(sampled, cv2.COLOR_BGR2HSV)
    histograms = []
    for part in (hsv[11:36], hsv[36:65]):
        # Hue is unstable on grey fabric. Pool near-neutral pixels in one bin.
        part = part.copy()
        part[:, :, 0][part[:, :, 1] < 32] = 0
        part[:, :, 1][part[:, :, 1] < 32] = 0
        histogram = cv2.calcHist([part], [0, 1], None, [16, 8], [0, 180, 0, 256])
        histograms.append(histogram / histogram.sum())
    return np.stack(histograms)


def appearance_distance(a: Descriptor, b: Descriptor) -> float:
    return sum(cv2.compareHist(x, y, cv2.HISTCMP_BHATTACHARYYA) for x, y in zip(a, b, strict=True)) / 2


@dataclass
class LocalGuest:
    seen_at: float
    guest_id: int | None = None
    descriptor: Descriptor | None = None
    samples: int = 0
    confidence: float | None = None
    floor: Point | None = None

    def learn(self, descriptor: Descriptor, config: IdentityConfig) -> None:
        self.samples += 1
        weight = 1 / self.samples if self.samples <= config.enroll_frames else config.descriptor_ema
        self.descriptor = descriptor if self.descriptor is None else (1 - weight) * self.descriptor + weight * descriptor


@dataclass
class Guest:
    descriptors: dict[str, Descriptor] = field(default_factory=dict)
    seen_at: float = 0
    floor: Point | None = None


class GlobalIdentityManager:
    """Associate session-local outfit descriptors without storing images."""

    def __init__(self, config: IdentityConfig) -> None:
        self.config = config
        self.locals: dict[tuple[str, int], LocalGuest] = {}
        self.guests: dict[int, Guest] = {}
        self.next_id = 1

    def retire(self, guest_ids: set[int]) -> None:
        for guest_id in guest_ids:
            self.guests.pop(guest_id, None)
        self.locals = {key: local for key, local in self.locals.items() if local.guest_id not in guest_ids}

    def update(
        self, camera: str, tracks: list[Track], descriptors: dict[int, Descriptor], now: float,
        floor: dict[int, Point],
    ) -> tuple[list[Track], list[Track], dict[int, float]]:
        self.locals = {
            key: local for key, local in self.locals.items()
            if now - local.seen_at <= self.config.relink_seconds
        }
        self.guests = {
            key: guest for key, guest in self.guests.items()
            if now - guest.seen_at <= self.config.relink_seconds
        }
        active = []
        for track in tracks:
            local = self.locals.setdefault((camera, track.id), LocalGuest(now))
            if local.guest_id not in self.guests:
                local.guest_id = None
            local.seen_at = now
            local.floor = floor.get(track.id)
            if track.id in descriptors:
                local.learn(descriptors[track.id], self.config)
            active.append(local)
        used = {local.guest_id for local in active if local.guest_id is not None}
        for local in active:
            if local.guest_id is not None:
                self._remember(camera, local, now)
        pending = [local for local in active if local.guest_id is None and local.samples >= self.config.enroll_frames]
        available = [(key, guest) for key, guest in self.guests.items() if key not in used]
        if pending and available:
            distances = np.array([[self._distance(camera, local, guest, now) for _, guest in available]
                                  for local in pending])
            admissible = distances.copy()
            for row, values in enumerate(distances):
                for col, value in enumerate(values):
                    second = min(
                        min(np.delete(values, col), default=2.0),
                        min(np.delete(distances[:, col], row), default=2.0),
                    )
                    if value > self.config.match_threshold or second - value < self.config.ambiguity_margin:
                        admissible[row, col] = 2.0
            _, columns, _ = lap.lapjv(admissible, extend_cost=True, cost_limit=self.config.match_threshold)
        else:
            distances = np.empty((len(pending), 0))
            columns = [-1] * len(pending)
        for index, local in enumerate(pending):
            col = columns[index]
            if col >= 0:
                local.guest_id = available[col][0]
                local.confidence = max(0.0, 1 - float(distances[index, col]))
                for (source, _), old in self.locals.items():
                    if source == camera and old is not local and old.guest_id == local.guest_id:
                        old.guest_id = None
            elif min(distances[index], default=2.0) > self.config.match_threshold:
                local.guest_id = self.next_id
                self.next_id += 1
                self.guests[local.guest_id] = Guest()
            if local.guest_id is not None:
                self._remember(camera, local, now)

        confirmed, provisional, confidence = [], [], {}
        for track, local in zip(tracks, active, strict=True):
            if local.guest_id is None:
                provisional.append(track)
            else:
                confirmed.append(Track(local.guest_id, track.kind, track.box))
                if local.confidence is not None:
                    confidence[local.guest_id] = local.confidence
        return confirmed, provisional, confidence

    def _distance(self, camera: str, local: LocalGuest, guest: Guest, now: float) -> float:
        if local.descriptor is None or not guest.descriptors:
            return 2.0
        if local.floor is not None and guest.floor is not None and now - guest.seen_at <= 0.5:
            if dist(local.floor, guest.floor) > self.config.max_floor_distance:
                return 2.0
        known = guest.descriptors.get(camera)
        if known is None:
            known = np.mean(list(guest.descriptors.values()), axis=0)
        return appearance_distance(local.descriptor, known)

    def _remember(self, camera: str, local: LocalGuest, now: float) -> None:
        assert local.guest_id is not None
        guest = self.guests[local.guest_id]
        if local.descriptor is not None:
            guest.descriptors[camera] = local.descriptor
        guest.seen_at = now
        if local.floor is not None:
            guest.floor = local.floor
