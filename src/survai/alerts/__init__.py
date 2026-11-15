import json
import logging
from dataclasses import asdict
from pathlib import Path
from time import time
from typing import Protocol

from ..events import Event
from .whatsapp import WhatsAppSink as WhatsAppSink

LOG = logging.getLogger(__name__)


class AlertSink(Protocol):
    def send(self, event: Event) -> None: ...


class ConsoleSink:
    def send(self, event: Event) -> None:
        level = logging.WARNING if event.severity == "warning" else logging.INFO
        LOG.log(level, "%s", event.message)


class JsonlSink:
    def __init__(self, directory: Path, filename: str = "events.jsonl") -> None:
        self.file = directory / filename

    def send(self, event: Event) -> None:
        with self.file.open("a", encoding="utf-8") as output:
            output.write(json.dumps(asdict(event)) + "\n")


def expire_snapshots(directory: Path, days: int) -> None:
    cutoff = time() - days * 86400
    for path in directory.glob("*.jpg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError as error:
            LOG.warning("Could not expire snapshot %s: %s", path.name, error)
