import json
import os
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from survai.detectors.walkout import WalkoutEvent
from survai.evidence import CLIP_FPS, RETENTION_INTERVAL, Evidence, snapshot_candidates
from survai.events import Event
from survai.server import create_app
from survai.state import State
from survai.tracks import Track


def test_delete_false_alarm_updates_history_and_survives_clip_completion(tmp_path: Path) -> None:
    state = State()
    alert = Event("possible_walkout", "warning", (1,), "", "", 0, clip_status="pending")
    other = Event("possible_walkout", "warning", (2,), "", "", 1)
    state.publish(b"", [alert, other], 1, 2, 0)
    with TestClient(create_app(state, tmp_path, summary_model=None)) as client:
        assert client.delete(f"/api/alerts/{alert.id}").status_code == 204
        assert client.delete(f"/api/alerts/{alert.id}").status_code == 204
        state.finish_clip(alert)
        remaining, = client.get("/api/alerts").json()
        assert remaining["id"] == other.id
        assert client.get("/api/status").json()["total_alerts"] == 1
        assert list(state.changes) == [(2, other), (3, alert.id)]


def test_evidence_retention_scopes_cleanup_and_updates_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("survai.evidence.time", lambda: now)
    clock = 0.0
    monkeypatch.setattr("survai.evidence.monotonic", lambda: clock)
    alert = Event("possible_walkout", "warning", (1,), "", "", 0, clip_status="ready")
    alert.snapshot_path = f"/snapshots/{alert.id}.jpg"
    alert.clip_path = f"/clips/{alert.id}.mp4"
    snapshot = tmp_path / f"{alert.id}.jpg"
    clip = tmp_path / f"{alert.id}.mp4"
    startup_expired = tmp_path / f"{'a' * 32}.mp4"
    preserved = [tmp_path / name for name in ("alerts.jsonl", "backup.mp4", f".{alert.id}.tmp.mp4")]
    for path in (startup_expired, *preserved, snapshot, clip):
        path.touch()
        modified = now if path in (snapshot, clip) else now - 5 * 86400
        os.utime(path, (modified, modified))
    symlink = tmp_path / f"{'b' * 32}.jpg"
    symlink.symlink_to(preserved[0])

    state = State()
    state.publish(b"", [alert], 0, 0, 0)
    evidence = Evidence(tmp_path, state, retention_days=3)
    try:
        assert not startup_expired.exists()
        assert snapshot.exists() and clip.exists()
        now += 3 * 86400 + 1
        clock += RETENTION_INTERVAL
        evidence.check_retention()
    finally:
        evidence.close(0)
    assert not snapshot.exists() and not clip.exists()
    assert all(path.exists() for path in (*preserved, symlink))
    (_, expired), = state.events
    assert expired.snapshot_path is None and expired.clip_path is None
    assert expired.clip_status == "expired"
    assert state.changes[-1][1] == expired


def test_alert_snapshot_and_clip_lifecycle(tmp_path: Path) -> None:
    state = State()
    evidence = Evidence(tmp_path, state)
    frame = np.full((90, 160, 3), 100, dtype=np.uint8)
    with TestClient(create_app(state, tmp_path, summary_model=None)) as client:
        assert client.get("/api/status").json()["total_alerts"] == 0
        try:
            guest = Track(7, "person", (0.15, 0.1, 0.45, 0.9))
            other_guest = Track(8, "person", (0.6, 0.1, 0.9, 0.9))
            clear_frame = frame.copy()
            clear_frame[9:81, 24:72] = (0, 0, 170)
            clear_frame[9:81, 24:72:6] = (100, 140, 255)
            clear_frame[9:81, 96:144] = (255, 0, 0)
            for index in range(8 * CLIP_FPS + 1):
                now = index / CLIP_FPS
                snapshots = {
                    5.5: (clear_frame, [guest, other_guest]),
                    6.25: (frame, [guest]),
                    6.75: (clear_frame, [other_guest]),
                }.get(now)
                candidates = snapshot_candidates(*snapshots, now) if snapshots else None
                evidence.observe(frame, now, snapshots=candidates)
            assert list(tmp_path.iterdir()) == []
            event = evidence.register(WalkoutEvent(
                track_id=7, timestamp=datetime.now(timezone.utc), source_seconds=8,
                dwell_seconds=4, exit_type="track_lost", message="Possible walk-out.",
                last_seen_seconds=6.5,
            ))
            state.publish(b"preview", [event], 8, 0, 0, alert_active=True)
            assert client.get("/api/status").json()["alert_active"]
            assert client.get("/api/status").json()["total_alerts"] == 1
            pending, = client.get("/api/alerts").json()
            assert pending["clip_status"] == "pending"
            assert event.snapshot_path is not None
            snapshot = client.get(event.snapshot_path)
            assert snapshot.headers["content-type"] == "image/jpeg"
            photo = cv2.imdecode(np.frombuffer(snapshot.content, dtype=np.uint8), cv2.IMREAD_COLOR)
            assert photo.shape == (72, 48, 3)
            assert photo[:, :, 2].mean() > 150
            assert event.clip_path is not None
            assert client.get(event.clip_path).status_code == 404

            for index in range(8 * CLIP_FPS + 1, 10 * CLIP_FPS + 1):
                evidence.observe(frame, index / CLIP_FPS)
            evidence.advance(10)
        finally:
            evidence.close(10)

        ready, = client.get("/api/alerts").json()
        assert ready["id"] == pending["id"]
        assert ready["clip_status"] == "ready"
        assert client.get("/api/status").json()["total_alerts"] == 1
        assert [(sequence, item.clip_status) for sequence, item in state.changes] == [
            (1, "pending"), (2, "ready"),
        ]
        response = client.get(event.clip_path, headers={"Range": "bytes=0-31"})
        assert response.status_code == 206
        assert response.headers["content-type"] == "video/mp4"
        assert client.get("/clips/alerts.jsonl").status_code == 404
        video = cv2.VideoCapture(str(tmp_path / f"{event.id}.mp4"))
        try:
            assert video.read()[0]
            assert video.get(cv2.CAP_PROP_FRAME_COUNT) == 10 * CLIP_FPS
        finally:
            video.release()
        assert json.loads((tmp_path / "alerts.jsonl").read_text())["clip_status"] == "ready"
