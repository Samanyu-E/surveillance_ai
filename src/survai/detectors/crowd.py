from ..events import Signal
from ..tracks import Track


class CrowdDetector:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self.active = False

    def update(self, tracks: list[Track], now: float) -> list[Signal]:
        ids = tuple(track.id for track in tracks if track.kind == "person")
        active = len(ids) >= self.threshold
        triggered = active and not self.active
        self.active = active
        return [Signal("crowd_surge", "info", ids, f"{len(ids)} people in view.")] if triggered else []
