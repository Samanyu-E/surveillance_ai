import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from survai.doorway import Crossing
from survai.events import Event
from survai.server import create_app
from survai.state import DoorActivity, State


def test_activity_log_combines_crossings_and_alerts_and_survives_restart(tmp_path: Path) -> None:
    state = State()
    app = create_app(state, tmp_path, None)
    stamp = datetime(2026, 11, 14, 20, 41, 5, tzinfo=timezone.utc)
    state.publish_door_counts(9, 6, 2, [
        DoorActivity(Crossing(7, "came in", 9, 5), stamp, False, 2),
        DoorActivity(Crossing(3, "left", 9, 6), stamp + timedelta(minutes=1), False, 2),
    ])
    event = Event("counter_wait", "warning", (7,), (stamp + timedelta(minutes=2)).isoformat(),
                  "Please assist the customer.", 5, dwell_seconds=5)
    state.publish(b"", [event], 5, 1, 0, True)
    with TestClient(app) as client:
        history = client.get("/api/status").json()["activity_log"]
    assert [entry["kind"] for entry in history] == ["alert", "door", "door"]
    assert history[0]["message"] == "ALERT: CUSTOMER #7 waiting at counter 5 sec"
    assert history[1]["message"] == "CUSTOMER #3 left | In store: 3 | Came in: 9 | Left: 6 | Employees: 2"
    assert history[2]["message"] == "CUSTOMER #7 came in | In store: 4 | Came in: 9 | Left: 5 | Employees: 2"
    state.finish_clip(event)
    assert len(state.activity.entries) == 3

    restarted = State()
    with TestClient(create_app(restarted, tmp_path, None)) as client:
        status = client.get("/api/status").json()
    assert status["door_counts"] is None
    assert status["activity_log"] == history
    assert len((tmp_path / "activity.jsonl").read_text().splitlines()) == 3


def test_existing_saved_alerts_are_recovered_once(tmp_path: Path) -> None:
    event = Event("counter_wait", "warning", (4,), "2026-11-14T20:43:50+00:00", "Waiting.",
                  120, dwell_seconds=120)
    (tmp_path / "alerts.jsonl").write_text(json.dumps(asdict(event)) + "\n", encoding="utf-8")
    for _ in range(2):
        state = State()
        with TestClient(create_app(state, tmp_path, None)) as client:
            entry, = client.get("/api/status").json()["activity_log"]
        assert entry["id"] == event.id
        assert entry["message"] == "ALERT: CUSTOMER #4 waiting at counter 2 min"
    assert len((tmp_path / "activity.jsonl").read_text().splitlines()) == 1


def test_malformed_history_lines_do_not_prevent_valid_entries_loading(tmp_path: Path) -> None:
    valid = {"id": "entry", "timestamp": "2026-11-14T20:43:50+00:00", "kind": "door", "message": "CUSTOMER #1 left"}
    (tmp_path / "activity.jsonl").write_text(
        "unfinished json\n" + json.dumps(valid) + "\n" + json.dumps(valid | {"timestamp": "bad"}) + "\n",
        encoding="utf-8",
    )
    with TestClient(create_app(State(), tmp_path, None)) as client:
        assert client.get("/api/status").json()["activity_log"] == [valid]
