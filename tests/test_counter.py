from pathlib import Path

import pytest
import numpy as np

from survai.config import WalkoutConfig, load_walkout_config
from survai.detectors.walkout import CounterWaitEvent, VisitState, WalkoutDetector
from survai.evidence import Evidence, snapshot_candidates
from survai.main import DEMO_CONFIG
from survai.multi_visit import RoomVisits, Sighting
from survai.server import create_app
from survai.state import State
from survai.synthetic import person
from fastapi.testclient import TestClient


@pytest.fixture
def config() -> WalkoutConfig:
    return load_walkout_config(DEMO_CONFIG)


def test_new_customer_waits_five_seconds_and_alerts_once(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    customer = person(1, 0.75, 0.7)
    for now in (0, 2, 4.99):
        assert not detector.update([customer], now)
        assert detector.visits[1].state == VisitState.NEW
    event, = detector.update([customer], 5)
    assert isinstance(event, CounterWaitEvent)
    assert event.dwell_seconds == 5 and event.exit_type is None
    assert not detector.update([customer], 20)
    assert detector.visits[1].state == VisitState.NEW
    assert not detector.update([person(1, 0.94, 0.7)], 21)
    assert not detector.update([], 23)


def test_counter_wait_restarts_after_leaving_or_losing_customer(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config)
    customer = person(1, 0.75, 0.7)
    detector.update([customer], 0)
    assert not detector.update([customer], 4)
    assert not detector.update([person(1, 0.75, 0.3)], 4.1)
    assert not detector.update([customer], 5)
    assert not detector.update([customer], 9.99)
    assert detector.update([customer], 10)
    detector.reset()
    detector.update([customer], 0)
    detector.update([], 4)
    assert not detector.update([customer], 4.1)
    assert not detector.update([customer], 9)
    assert detector.update([customer], 9.1)


def test_counter_wait_threshold_is_configurable(config: WalkoutConfig) -> None:
    detector = WalkoutDetector(config.model_copy(update={"counter_wait_seconds": 8}))
    customer = person(1, 0.75, 0.7)
    detector.update([customer], 0)
    assert not detector.update([customer], 5)
    assert detector.update([customer], 8)


def test_multicamera_counter_alert_is_shared_and_excludes_employees(config: WalkoutConfig) -> None:
    room = RoomVisits(config)
    views = {guest: [Sighting(camera, (0.75, 0.7), pay=True) for camera in ("a", "b")]
             for guest in (1, 2)}
    for now in (0, 2, 4.99):
        assert not room.update(views, now, coverage_ok=True, employee_ids={2})
    (camera, event), = room.update(views, 5, coverage_ok=True, employee_ids={2})
    assert camera == "a" and event.track_id == 1 and event.event_type == "counter_wait"
    assert room.visits[1].state == VisitState.NEW
    assert room.visits[2].state == VisitState.EMPLOYEE
    assert not room.update(views, 10, coverage_ok=True, employee_ids={2})


def test_multicamera_counter_wait_resets_on_absence(config: WalkoutConfig) -> None:
    room = RoomVisits(config)
    counter = {1: [Sighting("a", (0.75, 0.7), pay=True)]}
    room.update(counter, 0, coverage_ok=True)
    assert not room.update({}, 4, coverage_ok=False)
    assert not room.update(counter, 5, coverage_ok=True)
    assert not room.update(counter, 9.99, coverage_ok=True)
    assert len(room.update(counter, 10, coverage_ok=True)) == 1


def test_existing_diner_can_still_pay_without_counter_wait_alert(config: WalkoutConfig) -> None:
    room = RoomVisits(config)
    dining = {1: [Sighting("a", (0.4, 0.5), dining=True)]}
    counter = {1: [Sighting("a", (0.75, 0.7), pay=True)]}
    room.update(dining, 0, coverage_ok=True)
    room.update(dining, 3, coverage_ok=True)
    assert not room.update(counter, 4, coverage_ok=True)
    assert not room.update(counter, 6, coverage_ok=True)
    assert room.visits[1].state == VisitState.PAID
    assert not room.update(counter, 10, coverage_ok=True)


def test_legacy_zones_become_right_angled_rectangles(config: WalkoutConfig) -> None:
    settings = config.model_dump()
    slanted = [(0.1, 0.2), (0.4, 0.1), (0.5, 0.8), (0.2, 0.7)]
    for key in ("dining_zone", "pay_zone", "exit_band"):
        settings[key] = slanted
    rectangles = WalkoutConfig.model_validate(settings)
    for key in ("dining_zone", "pay_zone", "exit_band"):
        assert getattr(rectangles, key) == [(0.1, 0.1), (0.5, 0.1), (0.5, 0.8), (0.1, 0.8)]


def test_counter_alert_reaches_dashboard_with_correct_type(config: WalkoutConfig, tmp_path: Path) -> None:
    detector = WalkoutDetector(config)
    customer = person(1, 0.75, 0.7)
    detector.update([customer], 0)
    alert, = detector.update([customer], 5)
    state = State()
    evidence = Evidence(tmp_path, state)
    try:
        frame = np.full((100, 160, 3), 100, dtype=np.uint8)
        evidence.observe(frame, 4, snapshots=snapshot_candidates(frame, [customer], 4))
        event = evidence.register(alert)
        assert not evidence.pending
        state.publish(b"", [event], 5, 1, 0, True)
        with TestClient(create_app(state, tmp_path, None)) as client:
            item, = client.get("/api/alerts").json()
            assert item["type"] == "counter_wait" and item["exit_type"] is None
            assert item["dwell_seconds"] == 5 and "Please assist" in item["message"]
            assert item["snapshot_path"] is None and item["clip_path"] is None
            assert item["clip_status"] is None
            assert client.get("/api/status").json()["total_alerts"] == 1
            activity, = client.get("/api/status").json()["activity_log"]
            assert activity["message"] == "ALERT: CUSTOMER #1 waiting at counter 5 sec"
        evidence.advance(10)
    finally:
        evidence.close(10)
    assert not list(tmp_path.glob("*.jpg")) and not list(tmp_path.glob("*.mp4"))
    assert len((tmp_path / "alerts.jsonl").read_text().splitlines()) == 1
