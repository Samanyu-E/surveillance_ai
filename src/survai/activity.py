import json
import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .events import Event

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityEntry:
    id: str
    timestamp: str
    kind: str
    message: str


def alert_activity(event: Event) -> ActivityEntry:
    if event.type == "counter_wait":
        seconds = event.dwell_seconds or 0
        duration = f"{seconds / 60:g} min" if seconds >= 60 else f"{seconds:g} sec"
        customer = f" #{event.track_ids[0]}" if event.track_ids else ""
        message = f"ALERT: CUSTOMER{customer} waiting at counter {duration}"
    else:
        message = f"ALERT: {event.message}"
    try:
        datetime.fromisoformat(event.timestamp)
        timestamp = event.timestamp
    except ValueError:
        timestamp = datetime.now(timezone.utc).isoformat()
    return ActivityEntry(event.id, timestamp, "alert", message)


class ActivityLog:
    def __init__(self) -> None:
        self.entries: deque[ActivityEntry] = deque(maxlen=500)
        self.path: Path | None = None

    def open(self, directory: Path) -> None:
        path = directory / "activity.jsonl"
        if self.path == path:
            return
        pending = list(self.entries)
        self.entries.clear()
        self.path = path
        recovering_alerts = not path.exists()
        source = directory / "alerts.jsonl" if recovering_alerts else path
        try:
            if source.is_file():
                with source.open(encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            data = json.loads(line)
                            entry = alert_activity(Event(**data)) if recovering_alerts else ActivityEntry(**data)
                            datetime.fromisoformat(entry.timestamp)
                            if entry.kind not in {"door", "alert"} or not isinstance(entry.message, str):
                                continue
                        except (ValueError, TypeError, KeyError):
                            continue
                        if recovering_alerts:
                            self.append(entry, announce=False)
                        else:
                            self.entries.append(entry)
        except OSError:
            LOG.warning("Could not restore activity history", exc_info=True)
        for entry in pending:
            self.append(entry, announce=False)

    def append(self, entry: ActivityEntry, *, announce: bool = True) -> None:
        if any(previous.id == entry.id for previous in self.entries):
            return
        self.entries.append(entry)
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(asdict(entry)) + "\n")
            except OSError:
                LOG.warning("Could not save activity entry", exc_info=True)
        if announce:
            stamp = datetime.fromisoformat(entry.timestamp).astimezone().strftime("%I:%M:%S %p").lstrip("0")
            LOG.info("[%s] %s", stamp, entry.message)

    def newest_first(self) -> list[ActivityEntry]:
        return sorted(self.entries, key=lambda entry: datetime.fromisoformat(entry.timestamp).timestamp(), reverse=True)
