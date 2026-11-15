from pathlib import Path

import pytest

from survai.config import WalkoutConfig, load_walkout_config
from survai.detectors.walkout import VisitState, WalkoutDetector
from survai.synthetic import demo_tracks, person
from survai.tracks import Track


@pytest.fixture
def config() -> WalkoutConfig:
    return load_walkout_config(Path(__file__).parents[1] / "src/survai/walkout.demo.yaml")


def line_detector(config: WalkoutConfig) -> WalkoutDetector:
    settings = config.model_dump()
    settings.update(exit_band=None, exit_line=((0.88, 0.2), (0.88, 0.9)))
    return WalkoutDetector(WalkoutConfig.model_validate(settings))


def dine(detector: WalkoutDetector, track_id: int = 1, start: float = 0) -> None:
    assert not detector.update([person(track_id, 0.4)], start)
    assert not detector.update([person(track_id, 0.4)], start + 3)
    assert detector.visits[track_id].state == VisitState.DINING


def test_unpaid_line_exit_alerts(config: WalkoutConfig) -> None:
    detector = line_detector(config)
    dine(detector)
    assert not detector.update([person(1, 0.88)], 3.5)
    event, = detector.update([person(1, 0.94)], 4)
    assert (event.track_id, event.exit_type, event.dwell_seconds) == (1, "line", 3)


@pytest.mark.parametrize("manual", [False, True])
def test_paid_exit_stays_quiet(config: WalkoutConfig, manual: bool) -> None:
    detector = WalkoutDetector(config)
    if manual:
        dine(detector)
        detector.mark_dining_zone_paid(3)
        assert detector.visits[1].state == VisitState.PAID
        assert not detector.update([person(1, 0.94)], 4)
        assert not detector.update([], 5.5)
    else:
        for now, tracks in demo_tracks(paid=True):
            assert not detector.update(tracks, now)
            if now == 6:
                assert detector.visits[1].state == VisitState.PAID


def test_pass_through_does_not_qualify(config: WalkoutConfig) -> None:
    detector = line_detector(config)
    detector.update([person(1, 0.4)], 0)
    assert not detector.update([person(1, 0.94)], 2)


def test_vanishing_in_exit_band_alerts_after_grace(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    dine(detector)
    assert not detector.update([person(1, 0.94)], 4)
    assert not detector.update([], 5.49)
    event, = detector.update([], 5.5)
    assert event.exit_type == "track_lost"
    assert not detector.update([], 6)


def test_vanishing_mid_room_expires_silently(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    dine(detector)
    assert not detector.update([], 4.5)
    assert not detector.visits


def test_bottom_anchor_on_frame_boundary_counts_for_exit(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config.model_copy(update={"anchor": "bottom_center"}))
    dine(detector)
    assert not detector.update([Track(1, "person", (0.9, 0.4, 0.98, 1))], 4)
    assert detector.update([], 5.5)


def test_wrong_direction_does_not_alert(config: WalkoutConfig) -> None:
    detector = line_detector(config)
    dine(detector)
    assert not detector.update([person(1, 0.85, 0.1)], 3.1)
    assert not detector.update([person(1, 0.93, 0.1)], 3.2)
    assert not detector.update([person(1, 0.93, 0.5)], 3.3)
    assert not detector.update([person(1, 0.85, 0.5)], 3.4)
    assert detector.visits[1].state == VisitState.DINING


def test_global_cooldown_suppresses_duplicate(config: WalkoutConfig) -> None:
    detector = line_detector(config)
    dine(detector)
    assert detector.update([person(1, 0.94)], 4)
    dine(detector, track_id=2, start=5)
    assert not detector.update([person(2, 0.94)], 9)
    dine(detector, track_id=3, start=10)
    assert detector.update([person(3, 0.94)], 14)


def test_id_switch_keeps_payment(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    dine(detector)
    detector.mark_dining_zone_paid(3)
    assert not detector.update([], 3.2)
    assert not detector.update([person(2, 0.43)], 3.4)
    assert detector.visits[2].state == VisitState.PAID
    assert 1 not in detector.visits
    assert not detector.update([person(2, 0.94)], 4)
    assert not detector.update([], 5.5)


def test_return_to_exit_band_before_grace_does_not_alert(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    dine(detector)
    assert not detector.update([person(1, 0.94)], 4)
    assert not detector.update([], 4.25)
    assert not detector.update([person(1, 0.93)], 5)
    assert detector.visits[1].state == VisitState.DINING


def test_interrupted_dwell_restarts(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    detector.update([person(1, 0.4)], 0)
    detector.update([person(1, 0.4)], 2)
    detector.update([], 2.1)
    detector.update([person(1, 0.4)], 2.2)
    detector.update([person(1, 0.4)], 4)
    assert detector.visits[1].state == VisitState.NEW
