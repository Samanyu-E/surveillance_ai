from pathlib import Path

from survai.config import Config, load_config
from survai.detectors.bag import BagDetector
from survai.detectors.crowd import CrowdDetector
from survai.detectors.exit import ExitDetector
from survai.detectors.queue import QueueDetector
from survai.events import EventEngine, Signal
from survai.tracks import Track


def settings() -> Config:
    return load_config(Path(__file__).parents[1] / "config.yaml")


def person(track_id: int, x: float) -> Track:
    return Track(track_id, "person", (x - 0.02, 0.4, x + 0.02, 0.8))


def test_queue_and_wait() -> None:
    detector = QueueDetector(settings())
    group = [person(1, 0.2), person(2, 0.3), person(3, 0.4)]
    assert [s.type for s in detector.update(group, 0)] == ["long_queue"]
    assert len(detector.update(group, 10)) == 3
    assert not detector.update(group, 11)
    detector.update([], 12)
    assert not detector.update(group[:1], 13)


def test_crowd_rearms() -> None:
    detector = CrowdDetector(2)
    group = [person(1, 0.3), person(2, 0.5)]
    assert detector.update(group, 0)[0].type == "crowd_surge"
    assert not detector.update(group, 1)
    detector.update([], 2)
    assert detector.update(group, 3)


def test_exit_edge_paid_and_outsider() -> None:
    detector = ExitDetector(settings())
    assert not detector.update([person(1, 0.4), person(2, 0.4), person(3, 0.65)], 0)
    detector.mark_paid(2)
    assert not detector.update([person(1, 0.74)], 0.5)
    signals = detector.update([person(n, 0.9) for n in (1, 2, 3)], 1)
    assert [(s.type, s.track_ids) for s in signals] == [("possible_walkout", (1,))]


def test_attended_bag_restarts_timer() -> None:
    detector = BagDetector(settings())
    bag = Track(8, "backpack", (0.25, 0.74, 0.35, 0.84))
    assert not detector.update([bag], 0)
    assert not detector.update([bag, person(2, 0.3)], 4)
    assert not detector.update([bag], 5)
    assert detector.update([bag], 9)[0].type == "unattended_bag"
    assert not detector.update([bag], 10)


def test_finite_crossing_and_cooldown() -> None:
    from survai.zones import crosses

    assert not crosses((0, 0), (1, 0), ((0.5, 0.2), (0.5, 1)))
    engine = EventEngine(5)
    signal = Signal("test", "info", (1,), "test")
    assert engine.emit([signal, signal], 0)
    assert not engine.emit([signal], 4)
    assert engine.emit([signal], 5)
