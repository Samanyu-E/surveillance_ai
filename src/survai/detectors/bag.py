from dataclasses import dataclass
from math import dist

from ..config import Config
from ..events import Signal
from ..tracks import Track
from ..zones import Point


@dataclass
class Bag:
    anchor: Point
    since: float
    reported: bool = False


class BagDetector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bags: dict[int, Bag] = {}

    def update(self, tracks: list[Track], now: float) -> list[Signal]:
        people = [track.point for track in tracks if track.kind == "person"]
        bags = {}
        signals = []
        for track in tracks:
            if track.kind not in {"backpack", "handbag", "suitcase"}:
                continue
            if any(dist(track.point, person) <= self.config.bag_radius for person in people):
                continue
            bag = self.bags.get(track.id)
            if bag is None or dist(track.point, bag.anchor) > self.config.bag_motion:
                bag = Bag(track.point, now)
            bags[track.id] = bag
            if now - bag.since >= self.config.bag_seconds and not bag.reported:
                bag.reported = True
                signals.append(Signal(
                    "unattended_bag", "warning", (track.id,),
                    f"Unattended {track.kind} detected. Check the area.", (track.id,),
                ))
        self.bags = bags
        return signals
