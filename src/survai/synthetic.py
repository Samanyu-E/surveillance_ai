from collections.abc import Iterator

from .tracks import Track


def person(track_id: int, x: float, y: float = 0.5) -> Track:
    return Track(track_id, "person", (x - 0.03, y - 0.08, x + 0.03, y + 0.08))


def demo_tracks(paid: bool) -> Iterator[tuple[float, list[Track]]]:
    """Generate a 10 FPS demo take with ground-truth boxes."""
    for frame in range(91):
        now = frame / 10
        if frame <= 31:
            tracks = [person(1, 0.4)]
        elif frame <= 40:
            progress = (frame - 31) / 9
            tracks = [person(1, 0.4 + 0.35 * progress, 0.5 + 0.2 * progress)]
        elif frame <= 62:
            tracks = [person(1, 0.75, 0.7)] if paid else [person(1, 0.75, 0.5)]
            # The unpaid take passes above the pay zone on the way out.
        elif frame <= 70:
            x = 0.75 + 0.19 * (frame - 62) / 8
            tracks = [person(1, x, 0.7 if paid else 0.5)]
        else:
            tracks = []
        yield now, tracks
