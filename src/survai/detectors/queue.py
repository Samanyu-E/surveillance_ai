from ..config import Config
from ..events import Signal
from ..tracks import Track
from ..zones import inside


class QueueDetector:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.arrivals: dict[int, float] = {}
        self.active = False
        self.reported: set[int] = set()

    def update(self, tracks: list[Track], now: float) -> list[Signal]:
        ids = tuple(
            track.id for track in tracks
            if track.kind == "person" and inside(track.point, self.config.counter_zone)
        )
        self.arrivals = {track_id: self.arrivals.get(track_id, now) for track_id in ids}
        self.reported.intersection_update(ids)
        signals = []
        for track_id, arrival in self.arrivals.items():
            if now - arrival >= self.config.wait_seconds and track_id not in self.reported:
                self.reported.add(track_id)
                signals.append(Signal("long_wait", "warning", (track_id,),
                                      f"Counter wait reached {now - arrival:.1f}s.", (track_id,)))
        active = len(ids) >= self.config.queue_count
        if active and not self.active:
            signals.append(Signal("long_queue", "info", ids, f"{len(ids)} people in the queue."))
        self.active = active
        return signals
