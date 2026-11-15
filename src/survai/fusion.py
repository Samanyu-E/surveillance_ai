from dataclasses import dataclass, field
from itertools import combinations
from math import dist

import cv2
import lap
import numpy as np

from .config import FusionConfig
from .multi_identity import Descriptor, appearance_distance
from .tracks import Track
from .zones import Point


@dataclass(frozen=True)
class Detection:
    camera: str
    track: Track
    point: Point
    stamp: float
    appearance: Descriptor | None = None
    anchor_clipped: bool = False


def assign(costs: list[list[float]], limit: float) -> list[tuple[int, int]]:
    if not costs or not costs[0]:
        return []
    _, rows, _ = lap.lapjv(np.asarray(costs, dtype=float), extend_cost=True, cost_limit=limit)
    return [(row, int(col)) for row, col in enumerate(rows) if col >= 0]


def color_distance(a: Descriptor | None, b: Descriptor | None) -> float:
    return appearance_distance(a, b) if a is not None and b is not None else 0.0


@dataclass
class FusedObservation:
    detections: list[Detection]

    @property
    def point(self) -> Point:
        usable = [item.point for item in self.detections if not item.anchor_clipped]
        points = usable or [item.point for item in self.detections]
        x, y = np.mean(points, axis=0)
        return float(x), float(y)

    @property
    def appearance(self) -> Descriptor | None:
        return next((item.appearance for item in self.detections if item.appearance is not None), None)


def fuse(views: dict[str, list[Detection]], config: FusionConfig) -> list[FusedObservation]:
    groups: list[FusedObservation] = []
    for detections in views.values():
        costs = []
        for group in groups:
            row = []
            for item in detections:
                distance = dist(group.point, item.point)
                synchronized = all(abs(other.stamp - item.stamp) * 1000 <= config.max_skew_ms
                                   for other in group.detections)
                if distance > config.max_floor_dist or not synchronized:
                    row.append(2.0)
                    continue
                # Size is a weak cue because perspective differs between cameras.
                first = group.detections[0].track.box
                size_delta = abs((first[3] - first[1]) - (item.track.box[3] - item.track.box[1]))
                row.append(0.8 * distance / config.max_floor_dist
                           + config.color_weight * color_distance(group.appearance, item.appearance)
                           + 0.05 * size_delta)
            costs.append(row)
        matches = assign(costs, 1)
        if config.same_view_order and len(views) == 2 and len(matches) < len(groups):
            ordered = aligned_view_pairs(groups, detections, config)
            if ordered is not None:
                matches = ordered
        for row, col in matches:
            groups[row].detections.append(detections[col])
        used = {col for _, col in matches}
        groups.extend(FusedObservation([item]) for index, item in enumerate(detections) if index not in used)
    return groups


def aligned_view_pairs(
    groups: list[FusedObservation], detections: list[Detection], config: FusionConfig,
) -> list[tuple[int, int]] | None:
    """Recover pairs in same-facing views when truncation spoils projection."""
    if len(groups) != len(detections) or len(groups) < 2:
        return None
    first = [group.detections[0] for group in groups]
    if not any(item.anchor_clipped for item in first + detections):
        return None

    def horizontal_center(item: Detection) -> float:
        return (item.track.box[0] + item.track.box[2]) / 2

    pairs = list(zip(
        sorted(range(len(first)), key=lambda index: horizontal_center(first[index])),
        sorted(range(len(detections)), key=lambda index: horizontal_center(detections[index])),
        strict=True,
    ))
    for (left_a, left_b), (right_a, right_b) in zip(pairs, pairs[1:]):
        if (horizontal_center(first[right_a]) - horizontal_center(first[left_a]) < 0.08
                or horizontal_center(detections[right_b]) - horizontal_center(detections[left_b]) < 0.08):
            return None
    if any(abs(first[a].stamp - detections[b].stamp) * 1000 > config.max_skew_ms for a, b in pairs):
        return None
    return pairs


@dataclass
class FloorGuest:
    filter: cv2.KalmanFilter
    last_seen: float
    hits: int = 1
    number: int | None = None
    appearance: Descriptor | None = None
    trail: list[Point] = field(default_factory=list)
    local_keys: set[tuple[str, int]] = field(default_factory=set)

    @classmethod
    def start(cls, observation: FusedObservation, now: float) -> "FloorGuest":
        model = cv2.KalmanFilter(4, 2)
        model.statePost = np.float32([[*observation.point, 0, 0]]).T
        model.errorCovPost = np.diag(np.float32([0.01, 0.01, 0.5, 0.5]))
        model.measurementMatrix = np.eye(2, 4, dtype=np.float32)
        model.measurementNoiseCov = np.eye(2, dtype=np.float32) * 0.0005
        return cls(model, now, appearance=observation.appearance,
                   trail=[observation.point], local_keys=keys(observation))

    def predict(self, elapsed: float) -> Point:
        self.filter.transitionMatrix = np.float32([
            [1, 0, elapsed, 0], [0, 1, 0, elapsed], [0, 0, 1, 0], [0, 0, 0, 1],
        ])
        self.filter.processNoiseCov = np.diag(np.float32([0.001, 0.001, 0.035, 0.035])) * elapsed
        estimate = self.filter.predict()
        return float(estimate[0, 0]), float(estimate[1, 0])

    def update(self, observation: FusedObservation, now: float) -> None:
        self.filter.correct(np.float32([observation.point]).T)
        self.last_seen = now
        self.hits += 1
        if observation.appearance is not None:
            self.appearance = observation.appearance
        self.trail = (self.trail + [observation.point])[-24:]
        current_keys = keys(observation)
        observed_cameras = {camera for camera, _ in current_keys}
        self.local_keys = {key for key in self.local_keys if key[0] not in observed_cameras} | current_keys


def keys(observation: FusedObservation) -> set[tuple[str, int]]:
    return {(item.camera, item.track.id) for item in observation.detections}


def keep_linked_views(
    observations: list[FusedObservation], guests: list[FloorGuest], max_skew_ms: float,
) -> list[FusedObservation]:
    remaining = observations.copy()
    for guest in guests:
        linked = [group for group in remaining if keys(group) & guest.local_keys]
        if len(linked) < 2:
            continue
        detections = [item for group in linked for item in group.detections]
        if len({item.camera for item in detections}) != len(detections):
            continue
        stamps = [item.stamp for item in detections]
        if (max(stamps) - min(stamps)) * 1000 > max_skew_ms:
            continue
        if any(keys(group) & (other.local_keys - guest.local_keys)
               for group in linked for other in guests if other is not guest):
            continue
        for group in linked:
            remaining.remove(group)
        remaining.append(FusedObservation(detections))
    return remaining


class FloorTracker:
    def __init__(self, config: FusionConfig) -> None:
        self.config = config
        self.guests: list[FloorGuest] = []
        self.next_number = 1
        self.previous: float | None = None

    def update(
        self, observations: list[FusedObservation], now: float, *, allow_new: bool = True,
    ) -> dict[str, list[Track]]:
        elapsed = 0 if self.previous is None else now - self.previous
        self.previous = now
        self.guests = [guest for guest in self.guests if now - guest.last_seen <= self.config.forget_seconds]
        if self.config.same_view_order:
            observations = keep_linked_views(
                observations, [guest for guest in self.guests
                               if now - guest.last_seen <= self.config.lost_grace_seconds],
                self.config.max_skew_ms,
            )
        costs = []
        for guest in self.guests:
            predicted = guest.predict(elapsed)
            row = []
            for observation in observations:
                same_track = bool(guest.local_keys & keys(observation))
                missing = now - guest.last_seen
                # Long gaps get a tighter relink gate near the last observed position.
                target = predicted if missing <= self.config.lost_grace_seconds else guest.trail[-1]
                distance = dist(target, observation.point)
                gate = self.config.max_floor_dist * (1.0 if missing <= self.config.lost_grace_seconds else 0.6)
                cost = (0.8 * distance / gate
                        + self.config.color_weight * color_distance(guest.appearance, observation.appearance)
                        - (0.08 if same_track else 0))
                if same_track and missing <= self.config.lost_grace_seconds:
                    # A camera-local track can survive a large projected jump
                    # when the other view is added or drops out.
                    cost = min(cost, 0.75) if distance <= 2 * gate else 2.0
                elif distance > gate:
                    cost = 2.0
                row.append(cost)
            costs.append(row)
        matches = assign(costs, 1)
        active = []
        matched_guests = {row for row, _ in matches}
        for row, col in matches:
            guest = self.guests[row]
            guest.update(observations[col], now)
            active.append((guest, observations[col]))
        self.guests = [guest for index, guest in enumerate(self.guests)
                       if index in matched_guests or guest.number is not None]
        matched_observations = {col for _, col in matches}
        for col, observation in enumerate(observations):
            if allow_new and col not in matched_observations:
                guest = FloorGuest.start(observation, now)
                self.guests.append(guest)
                active.append((guest, observation))
        result: dict[str, list[Track]] = {}
        for guest, observation in active:
            if guest.number is None and guest.hits >= self.config.min_hits:
                guest.number = self.next_number
                self.next_number += 1
            if guest.number is not None:
                for item in observation.detections:
                    result.setdefault(item.camera, []).append(Track(guest.number, item.track.kind, item.track.box))
        return result

    def retire(self, numbers: set[int]) -> None:
        self.guests = [guest for guest in self.guests if guest.number not in numbers]


def synchronized_frames(options: dict[str, list[float]], max_skew_ms: float) -> dict[str, float]:
    """Choose the largest set of feeds with compatible timestamps, biased to newer pairs."""
    if not options:
        return {}
    for size in range(len(options), 1, -1):
        best: dict[str, float] = {}
        for cameras in combinations(options, size):
            for anchor in (stamp for camera in cameras for stamp in options[camera]):
                selected = {camera: max((stamp for stamp in options[camera]
                                         if anchor <= stamp <= anchor + max_skew_ms / 1000), default=-1)
                            for camera in cameras}
                if min(selected.values()) >= 0 and (not best or min(selected.values()) > min(best.values())):
                    best = selected
        if best:
            return best
    camera = max(options, key=lambda key: max(options[key]))
    return {camera: max(options[camera])}
