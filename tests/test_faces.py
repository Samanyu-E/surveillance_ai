from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from survai.detectors.walkout import CounterWaitEvent, WalkoutEvent
from survai.evidence import Evidence, SnapshotCandidate, expire_evidence
from survai.faces import FaceExtractor, FaceThumbnail
from survai.server import create_app
from survai.state import State


def photo() -> bytes:
    frame = np.random.default_rng(7).integers(50, 180, (256, 128, 3), dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok
    return jpeg.tobytes()


def extractor(rectangles: list[tuple[int, int, int, int]]) -> FaceExtractor:
    faces = FaceExtractor()
    faces.loaded = True
    faces.detector = SimpleNamespace(empty=lambda: False, detectMultiScale=lambda *_args, **_kw: rectangles)
    return faces


def test_detected_head_is_cropped_without_replacing_the_person_photo() -> None:
    original = photo()
    face = extractor([(30, 14, 50, 50)]).best([original])
    assert face is not None
    crop = cv2.imdecode(np.frombuffer(face.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert 50 <= crop.shape[0] < 100 and 50 <= crop.shape[1] < 100
    assert original == photo()


@pytest.mark.parametrize("rectangles", [
    [], [(12, 12, 25, 25), (72, 10, 25, 25)], [(30, 180, 50, 50)], [(-4, 14, 50, 50)],
])
def test_missing_ambiguous_or_misplaced_faces_use_person_fallback(rectangles) -> None:
    assert extractor(rectangles).best([photo()]) is None


def test_blurred_dark_and_corrupt_crops_are_not_faces() -> None:
    faces = extractor([(30, 14, 50, 50)])
    for value in (5, 110):
        ok, jpeg = cv2.imencode(".jpg", np.full((256, 128, 3), value, dtype=np.uint8))
        assert ok
        assert faces.best([jpeg.tobytes()]) is None
    assert faces.best([b"corrupt"]) is None


def test_bundled_detector_handles_a_frame_without_a_face() -> None:
    faces = FaceExtractor()
    assert faces.best([photo()]) is None
    assert faces.detector is not None and not faces.detector.empty()


def test_face_and_original_snapshot_are_served_and_expire_independently(tmp_path: Path) -> None:
    state = State()
    evidence = Evidence(tmp_path, state)
    faces = extractor([(30, 14, 50, 50)])
    evidence.faces = faces
    original = photo()
    evidence.snapshots.append({7: SnapshotCandidate(4, 1, original)})
    try:
        event = evidence.register(WalkoutEvent(7, datetime.now(timezone.utc), 8, 4, "track_lost", "Exit", 6))
        assert event.snapshot_path is not None and event.face_path is not None
        state.publish(b"", [event], 8, 0, 0)
        with TestClient(create_app(state, tmp_path, summary_model=None)) as client:
            assert client.get(event.snapshot_path).content == original
            face = client.get(event.face_path)
            assert face.status_code == 200 and face.headers['content-type'] == 'image/jpeg'
            assert client.get('/snapshots/invalid-face.jpg').status_code == 404
            saved, = client.get('/api/alerts').json()
            assert saved['face_path'] == event.face_path
            state.expire_evidence({event.face_path})
            expired, = client.get('/api/alerts').json()
            assert expired['face_path'] is None and expired['snapshot_path'] == event.snapshot_path
            assert expired['clip_status'] == 'pending'
    finally:
        evidence.close(8)


def test_face_search_stays_with_the_guest_and_before_their_last_sighting(tmp_path: Path) -> None:
    evidence = Evidence(tmp_path, State())
    search = Mock(return_value=FaceThumbnail(photo(), 1))
    evidence.faces.best = search
    for seconds in (1, 1.1, 2, 2.1, 6.5):
        evidence.snapshots.append({7: SnapshotCandidate(seconds, seconds, str(seconds).encode())})
    evidence.snapshots.append({7: SnapshotCandidate(3, 100, b"overlapping", face_eligible=False)})
    evidence.snapshots.append({8: SnapshotCandidate(4, 100, b"another-person")})
    try:
        evidence.register(WalkoutEvent(7, datetime.now(timezone.utc), 8, 4, "track_lost", "Exit", 6))
        assert list(search.call_args.args[0]) == [b"2.1", b"1.1"]
    finally:
        evidence.close(8)


def test_counter_wait_does_not_run_face_detection_or_save_media(tmp_path: Path) -> None:
    evidence = Evidence(tmp_path, State())
    evidence.faces.best = Mock(side_effect=AssertionError("Counter alerts must remain text only"))
    evidence.snapshots.append({7: SnapshotCandidate(4, 1, photo())})
    try:
        event = evidence.register(CounterWaitEvent(7, datetime.now(timezone.utc), 5, 5, "Waiting", 5))
        assert event.face_path is None and event.snapshot_path is None and event.clip_path is None
        assert not list(tmp_path.glob('*.jpg')) and not evidence.pending
    finally:
        evidence.close(5)


def test_retention_removes_face_files_and_keeps_unrelated_files(tmp_path: Path) -> None:
    face = tmp_path / f"{'a' * 32}-face.jpg"
    unrelated = tmp_path / 'notes-face.jpg'
    face.write_bytes(b'face')
    unrelated.write_bytes(b'notes')
    deleted = expire_evidence(tmp_path, 1, now=face.stat().st_mtime + 86401)
    assert deleted == {f'/snapshots/{face.name}'}
    assert not face.exists() and unrelated.exists()
