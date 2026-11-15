from collections.abc import Callable
from dataclasses import dataclass
from math import dist
from typing import Literal

from .tracks import Track
from .zones import Point, crosses, side

LOST_AFTER = 0.75
EDGE_CLIP = 0.02
MAX_EDGE_DISTANCE = 0.14
MIN_INWARD_MOTION = 0.025


@dataclass
class Observation:
    stable: Point
    seen_at: float


@dataclass(frozen=True)
class Crossing:
    track_id: int
    direction: Literal["came in", "left"]
    came_in: int
    left: int

    @property
    def in_store(self) -> int:
        return max(0, self.came_in - self.left)


class DoorCounter:
    """Count crossings of the drawn segment. Its positive side faces inside."""

    def __init__(self, line: tuple[Point, Point]) -> None:
        self.line = line
        self.length = dist(*line)
        self.band = self.length * 0.015
        self.observations: dict[int, Observation] = {}
        self.came_in = 0
        self.left = 0

    def update(
        self, points: dict[int, Point], now: float, *,
        initial_points: dict[int, list[Point]] | None = None,
    ) -> list[Crossing]:
        crossings = []
        self.observations = {
            key: value for key, value in self.observations.items() if now - value.seen_at <= LOST_AFTER
        }
        for guest, point in points.items():
            for earlier in (initial_points or {}).get(guest, ()):
                if crossing := self._observe(guest, earlier, now):
                    crossings.append(crossing)
            if crossing := self._observe(guest, point, now):
                crossings.append(crossing)
        return crossings

    def update_tracks(
        self, tracks: list[Track], paths: dict[int, list[Track]],
        anchor: Callable[[Track], Point], now: float,
    ) -> list[Crossing]:
        initial_points = {}
        for guest, history in paths.items():
            points = [anchor(sample) for sample in history]
            if edge := self._entry_edge(history[0], points):
                points.insert(0, edge)
            initial_points[guest] = points[:-1]  # The current point is supplied by tracks.
        return self.update({track.id: anchor(track) for track in tracks}, now, initial_points=initial_points)

    def _entry_edge(self, first_track: Track, points: list[Point]) -> Point | None:
        # Near a frame edge, the first detected box can already straddle the
        # line. Its clipped side and subsequent inward motion establish entry.
        first, last = points[0], points[-1]
        offsets = [side(*self.line, point) for point in points]
        if (
            min(offsets) < -self.band
            or offsets[0] > MAX_EDGE_DISTANCE * self.length
            or offsets[-1] <= self.band
            or offsets[-1] - offsets[0] < MIN_INWARD_MOTION * self.length
        ):
            return None
        left, top, right, bottom = first_track.box
        candidates = (
            (left <= EDGE_CLIP, (0.0, first[1])),
            (right >= 1 - EDGE_CLIP, (1.0, first[1])),
            (top <= EDGE_CLIP, (first[0], 0.0)),
            (bottom >= 1 - EDGE_CLIP, (first[0], 1.0)),
        )
        return next((
            edge for clipped, edge in candidates
            if clipped and -MAX_EDGE_DISTANCE * self.length <= side(*self.line, edge) < -self.band
            and crosses(edge, last, self.line)
        ), None)

    def _observe(self, guest: int, point: Point, now: float) -> Crossing | None:
        offset = side(*self.line, point)
        previous = self.observations.get(guest)
        if abs(offset) <= self.band:
            if previous is not None:
                previous.seen_at = now
            return None
        self.observations[guest] = Observation(point, now)
        if previous is None or not crosses(previous.stable, point, self.line):
            return None
        if offset > 0:
            self.came_in += 1
            return Crossing(guest, "came in", self.came_in, self.left)
        self.left += 1
        return Crossing(guest, "left", self.came_in, self.left)
