from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

Severity = Literal["info", "warning"]


@dataclass(frozen=True)
class Signal:
    type: str
    severity: Severity
    track_ids: tuple[int, ...]
    message: str
    key: tuple[int, ...] = ()


@dataclass
class Event:
    type: str
    severity: Severity
    track_ids: tuple[int, ...]
    timestamp: str
    message: str
    source_seconds: float
    id: str = field(default_factory=lambda: uuid4().hex)
    snapshot_path: str | None = None
    clip_path: str | None = None
    clip_status: Literal["pending", "ready", "unavailable", "expired"] | None = None
    dwell_seconds: float | None = None
    exit_type: str | None = None
    face_path: str | None = None


class EventEngine:
    def __init__(self, cooldown: float) -> None:
        self.cooldown = cooldown
        self.recent: dict[tuple[str, tuple[int, ...]], float] = {}

    def emit(self, signals: list[Signal], now: float) -> list[Event]:
        self.recent = {
            key: then for key, then in self.recent.items() if now - then < self.cooldown
        }
        events = []
        for signal in signals:
            key = (signal.type, signal.key)
            if key in self.recent:
                continue
            self.recent[key] = now
            events.append(Event(
                signal.type, signal.severity, signal.track_ids,
                datetime.now(timezone.utc).isoformat(), signal.message, round(now, 3),
            ))
        return events
