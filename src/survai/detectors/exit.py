from dataclasses import dataclass

from ..config import Config
from ..events import Signal
from ..tracks import Track
from ..zones import Point, crosses, inside, side


@dataclass
class Visit:
    point: Point
    seen: float
    paid: bool = False
    armed: bool = False


class ExitDetector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.visits: dict[int, Visit] = {}

    def mark_paid(self, track_id: int) -> None:
        if track_id in self.visits:
            self.visits[track_id].paid = True

    def update(self, tracks: list[Track], now: float) -> list[Signal]:
        signals = []
        self.visits = {
            track_id: visit for track_id, visit in self.visits.items() if now - visit.seen <= 2
        }
        for track in tracks:
            if track.kind != "person":
                continue
            point = track.point
            visit = self.visits.setdefault(track.id, Visit(point, now))
            if visit.armed and crosses(visit.point, point, self.config.exit_line):
                if not visit.paid:
                    signals.append(Signal(
                        "possible_walkout", "warning", (track.id,),
                        "Possible walk-out. Verify whether the guest paid.", (track.id,),
                    ))
                visit.armed = False
                visit.paid = False
            if inside(point, self.config.counter_zone):
                visit.armed = True
            if side(*self.config.exit_line, point) != 0:
                visit.point = point
            visit.seen = now
        return signals
