from typing import Protocol

from ..events import Signal
from ..tracks import Track


class Detector(Protocol):
    def update(self, tracks: list[Track], now: float) -> list[Signal]: ...
