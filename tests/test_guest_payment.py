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
from survai.profiles import Profile, VisitProfiles
from survai.server import create_app
from survai.state import State
from survai.synthetic import person


def test_pay_one_guest_updates_api_and_overlay_and_prevents_walkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_walkout_config(DEMO_CONFIG)
    room = RoomVisits(config)
    tracks = [person(1, .4), person(2, .6)]
    views = {track.id: [Sighting('cam1', track.point, dining=True)] for track in tracks}
    room.update(views, 0, coverage_ok=True)
    room.update(views, 4, coverage_ok=True)
    assert all(visit.state == VisitState.DINING for visit in room.visits.values())
    profiles = VisitProfiles()
    profiles.update(tracks, room.visits, datetime.now(timezone.utc))
    state = State()
    state.publish_profiles(profiles.active)
    state.publish(b'frame', [], 4, 2, 0)
    with TestClient(create_app(state, tmp_path, None)) as client:
        assert client.post('/api/visits/1/pay').status_code == 202
        assert client.post('/api/visits/1/pay').status_code == 202
        assert state.take_payments() == {1}
        assert not state.take_payments()
        room.mark_guest_paid(1, 4.1)
        room.update(views, 4.2, coverage_ok=True)
        profiles.update(tracks, room.visits, datetime.now(timezone.utc))
        state.publish_profiles(profiles.active)
        statuses = {guest['track_id']: guest['state'] for guest in client.get('/api/visits').json()}
        assert statuses == {1: 'PAID', 2: 'DINING'}
        assert client.post('/api/visits/1/pay').status_code == 202
        assert not state.take_payments()

    hud = WalkoutDetector(config)
    hud.visits = room.visits
    labels = []
    monkeypatch.setattr('survai.hud.cv2.putText', lambda image, text, *args: labels.append(text))
    annotate_walkout(np.zeros((240, 320, 3), np.uint8), tracks, hud, 12, False, global_labels=True)
    assert 'Guest 1 PAID' in labels and 'Guest 2 DINING' in labels
    room.update({1: [Sighting('cam1', (.95, .6), exit='track_lost')], 2: views[2]}, 5, coverage_ok=True)
    assert not room.update({2: views[2]}, 6, coverage_ok=True)
    assert not room.update({2: views[2]}, 6 + config.lost_grace_seconds, coverage_ok=True)


@pytest.mark.parametrize('state_name,employee,visible,status,walkout,expected', [
    ('EMPLOYEE', True, True, 'Monitoring', True, 409),
    ('EMPLOYEE', False, True, 'Monitoring', True, 409),
    ('EXITED', False, True, 'Monitoring', True, 404),
    ('DINING', False, False, 'Monitoring', True, 409),
    ('DINING', False, True, 'Monitoring stopped', True, 409),
    ('DINING', False, True, 'Monitoring', False, 409),
])
def test_payment_rejects_ineligible_guest_or_session(
    tmp_path: Path, state_name, employee, visible, status, walkout, expected,
) -> None:
    state = State()
    now = datetime.now(timezone.utc)
    state.profiles[1] = Profile(1, now, now, VisitState(state_name), visible=visible, employee=employee)
    state.status = status
    state.walkout_enabled = walkout
    with TestClient(create_app(state, tmp_path, None)) as client:
        assert client.post('/api/visits/1/pay').status_code == expected
    assert not state.take_payments()


def test_missing_guest_is_not_queued(tmp_path: Path) -> None:
    state = State()
    state.status = 'Monitoring'
    with TestClient(create_app(state, tmp_path, None)) as client:
        assert client.post('/api/visits/999/pay').status_code == 404
    assert not state.take_payments()


@pytest.mark.parametrize('runtime', [RoomVisits, WalkoutDetector])
def test_confirm_new_guest_at_counter_and_ignore_missing_or_employee(runtime) -> None:
    config = load_walkout_config(DEMO_CONFIG)
    monitor = runtime(config)
    if isinstance(monitor, RoomVisits):
        views = {1: [Sighting('cam1', (.75, .7), pay=True)], 2: [Sighting('cam1', (.3, .5))]}
        monitor.update(views, 0, coverage_ok=True, employee_ids={2})
    else:
        views = [person(1, .75, .7), person(2, .3, .5)]
        monitor.update(views, 0)
        monitor.visits[2].state = VisitState.EMPLOYEE
    assert not monitor.mark_guest_paid(999, 1)
    assert not monitor.mark_guest_paid(2, 1)
    assert monitor.mark_guest_paid(1, 1)
    assert monitor.visits[1].state == VisitState.PAID
    assert monitor.visits[2].state == VisitState.EMPLOYEE
    if isinstance(monitor, RoomVisits):
        assert not monitor.update(views, 10, coverage_ok=True, employee_ids={2})
    else:
        assert not monitor.update(views, 10)
    assert monitor.visits[1].state == VisitState.PAID
