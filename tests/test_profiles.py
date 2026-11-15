from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from survai.config import load_walkout_config
from survai.detectors.walkout import WalkoutDetector
from survai.main import DEMO_CONFIG
from survai.profiles import VisitProfiles
from survai.server import create_app
from survai.state import State
from survai.synthetic import person
from survai.tracks import Track


def test_paid_visit_has_no_photo_and_is_removed_after_exit(tmp_path: Path) -> None:
    config = load_walkout_config(DEMO_CONFIG)
    detector = WalkoutDetector(config)
    profiles = VisitProfiles()
    state = State()
    start = datetime(2026, 11, 13, tzinfo=timezone.utc)

    def step(seconds: float, tracks: list[Track]) -> None:
        assert not detector.update(tracks, seconds, occluded_ids=set())
        profiles.update(tracks, detector.visits, start + timedelta(seconds=seconds))
        state.publish_profiles(profiles.active)

    with TestClient(create_app(state, tmp_path, None)) as client:
        step(0, [person(4, 0.4)])
        assert client.get("/api/visits").json()[0]["state"] == "NEW"
        step(3, [person(4, 0.4)])
        detector.mark_dining_zone_paid(3.1)
        step(3.1, [person(4, 0.4)])
        profile, = client.get("/api/visits").json()
        assert profile["state"] == "PAID"
        assert "photo_path" not in profile
        assert client.get("/api/visits/4/photo").status_code == 404
        step(4, [person(4, 0.94)])
        step(5.6, [])
        assert client.get("/api/visits").json() == []
        assert client.get("/api/alerts").json() == []
    assert list(tmp_path.iterdir()) == []
