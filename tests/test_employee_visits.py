from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from survai.config import load_walkout_config
from survai.detectors.walkout import VisitState, WalkoutDetector
from survai.hud import annotate_walkout
from survai.main import DEMO_CONFIG
from survai.multi_visit import RoomVisits, Sighting
from survai.profiles import VisitProfiles
from survai.server import create_app
from survai.state import State
from survai.synthetic import person


@pytest.mark.parametrize("prior_state", [VisitState.NEW, VisitState.DINING, VisitState.PAID])
def test_recognized_employee_has_no_guest_states_or_customer_alerts(prior_state: VisitState) -> None:
    room = RoomVisits(load_walkout_config(DEMO_CONFIG))
    dining = {1: [Sighting("cam1", (0.4, 0.5), dining=True)]}
    room.update(dining, 0, coverage_ok=True)
    room.visits[1].state = prior_state
    for now in (1, 5):
        assert not room.update(dining, now, coverage_ok=True, employee_ids={1})
        assert room.visits[1].state == VisitState.EMPLOYEE
        assert room.visits[1].dining_seconds == 0
        room.mark_paid(now)
        assert room.visits[1].state == VisitState.EMPLOYEE
    counter = {1: [Sighting("cam1", (0.75, 0.7), pay=True)]}
    for now in (6, 12):
        assert not room.update(counter, now, coverage_ok=True, employee_ids={1})
        assert room.visits[1].state == VisitState.EMPLOYEE
    exit_view = {1: [Sighting("cam1", (0.94, 0.7), exit="track_lost")]}
    assert not room.update(exit_view, 13, coverage_ok=True, employee_ids={1})
    for now in (14, 16):
        assert not room.update({}, now, coverage_ok=True)
    assert not room.visits


def test_employee_profile_remains_labeled_out_of_view(tmp_path: Path) -> None:
    room = RoomVisits(load_walkout_config(DEMO_CONFIG))
    room.update({1: [Sighting("cam1", (0.4, 0.5), dining=True)]}, 0, coverage_ok=True, employee_ids={1})
    profiles = VisitProfiles()
    profiles.update([], room.visits, datetime.now(timezone.utc))
    state = State()
    state.publish_profiles(profiles.active)
    with TestClient(create_app(state, tmp_path, None)) as client:
        profile, = client.get("/api/visits").json()
    assert profile["employee"] is True
    assert profile["state"] == "EMPLOYEE" and profile["visible"] is False


def test_reclassified_customer_starts_a_fresh_guest_visit() -> None:
    room = RoomVisits(load_walkout_config(DEMO_CONFIG))
    dining = {1: [Sighting("cam1", (0.4, 0.5), dining=True)]}
    room.update(dining, 0, coverage_ok=True, employee_ids={1})
    room.update(dining, 5, coverage_ok=True)
    assert room.visits[1].state == VisitState.NEW
    room.update(dining, 8, coverage_ok=True)
    assert room.visits[1].state == VisitState.DINING


def test_camera_overlay_shows_employee_without_guest_status(monkeypatch: pytest.MonkeyPatch) -> None:
    detector = WalkoutDetector(load_walkout_config(DEMO_CONFIG))
    track = person(1, 0.4)
    detector.update([track], 0)
    detector.update([track], 3)
    labels = []
    monkeypatch.setattr("survai.hud.cv2.putText", lambda image, text, *args: labels.append(text))
    annotate_walkout(np.zeros((240, 320, 3), np.uint8), [track], detector, 12, False, employees={1})
    assert "Employee 1" in labels
    assert not any("DINING" in text and "Employee" in text for text in labels)
