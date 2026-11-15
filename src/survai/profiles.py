from dataclasses import dataclass
from datetime import datetime

from .detectors.walkout import Visit, VisitState
from .tracks import Track


@dataclass
class Profile:
    track_id: int
    first_seen: datetime
    last_seen: datetime
    state: VisitState
    visible: bool = True
    cameras: tuple[str, ...] = ()
    dwell_seconds: float | None = None
    match_confidence: float | None = None
    employee: bool = False


class VisitProfiles:
    def __init__(self) -> None:
        self.active: dict[int, Profile] = {}

    def clear(self) -> None:
        self.active.clear()

    def update(
        self, tracks: list[Track], visits: dict[int, Visit], captured_at: datetime,
    ) -> None:
        visible = {track.id for track in tracks}
        for track_id in list(self.active):
            if track_id not in visits or visits[track_id].state == VisitState.EXITED:
                del self.active[track_id]
        for track_id, visit in visits.items():
            if visit.state == VisitState.EXITED:
                continue
            profile = self.active.setdefault(
                track_id, Profile(track_id, captured_at, captured_at, visit.state),
            )
            profile.state = visit.state
            profile.employee = visit.state == VisitState.EMPLOYEE
            profile.visible = track_id in visible
            if profile.visible:
                profile.last_seen = captured_at
