from dataclasses import dataclass

from .zones import Point


@dataclass(frozen=True)
class Track:
    id: int
    kind: str
    box: tuple[float, float, float, float]

    @property
    def point(self) -> Point:
        left, top, right, bottom = self.box
        return ((left + right) / 2, bottom if self.kind == "person" else (top + bottom) / 2)


