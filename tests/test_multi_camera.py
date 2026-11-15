from pathlib import Path

import numpy as np
import pytest

from survai.config import IdentityConfig, load_walkout_config
from survai.detectors.walkout import VisitState
from survai.multi_identity import GlobalIdentityManager
from survai.multi_test import selftest_multi
from survai.multi_visit import RoomVisits, Sighting
from survai.tracks import Track

CONFIG = Path(__file__).parents[1] / "src/survai/walkout.demo.yaml"


def test_two_camera_selftest() -> None:
    assert selftest_multi(load_walkout_config(CONFIG))


def test_ambiguous_track_does_not_consume_a_guest_number() -> None:
    identities = GlobalIdentityManager(IdentityConfig(enroll_frames=2))
    outfit = np.ones((2, 16, 8), dtype=np.float32) / 128
    first = Track(1, "person", (0.1, 0.1, 0.3, 0.8))
    second = Track(2, "person", (0.6, 0.1, 0.8, 0.8))
    assert not identities.update("front", [first, second], {1: outfit, 2: outfit}, 0, {})[0]
    assert identities.next_id == 1
    confirmed, _, _ = identities.update("front", [first, second], {1: outfit, 2: outfit}, 0.1, {})
    assert [track.id for track in confirmed] == [1, 2]
    identities.update("side", [first], {1: outfit}, 0.2, {})
    confirmed, pending, _ = identities.update("side", [first], {1: outfit}, 0.3, {})
    assert not confirmed and pending == [first]
    assert identities.next_id == 3


def test_relink_releases_old_same_camera_mapping() -> None:
    identities = GlobalIdentityManager(IdentityConfig(enroll_frames=1))
    outfit = np.ones((2, 16, 8), dtype=np.float32) / 128
    original = Track(1, "person", (0.1, 0.1, 0.3, 0.8))
    relinked = Track(2, "person", (0.2, 0.1, 0.4, 0.8))
    assert identities.update("front", [original], {1: outfit}, 0, {})[0][0].id == 1
    assert identities.update("front", [relinked], {2: outfit}, 1, {})[0][0].id == 1
    tracks, _, _ = identities.update("front", [original, relinked], {1: outfit, 2: outfit}, 2, {})
    assert len({track.id for track in tracks}) == len(tracks)
    assert tracks[-1].id == 1


@pytest.mark.parametrize("paid", [False, True])
def test_room_exit_waits_for_other_camera_and_respects_payment(paid: bool) -> None:
    room = RoomVisits(load_walkout_config(CONFIG))
    dining = {1: [Sighting("cam1", (0.4, 0.5), dining=True), Sighting("cam2", (0.5, 0.5), dining=True)]}
    room.update(dining, 0, coverage_ok=True)
    room.update(dining, 3, coverage_ok=True)
    assert room.visits[1].dining_seconds == 3
    if paid:
        pay = {1: [Sighting("cam2", (0.76, 0.7), pay=True)]}
        room.update(pay, 4, coverage_ok=True)
        room.update(pay, 6, coverage_ok=True)
        assert room.visits[1].state == VisitState.PAID
    exit_view = Sighting("cam1", (0.94, 0.5), exit="track_lost")
    assert not room.update({1: [exit_view, Sighting("cam2", (0.5, 0.5))]}, 7, coverage_ok=True)
    assert not room.update({1: [Sighting("cam2", (0.5, 0.5))]}, 9, coverage_ok=True)
    assert 1 in room.visits
    assert not room.update({1: [exit_view, Sighting("cam2", (0.94, 0.5), exit="track_lost")]}, 10, coverage_ok=True)
    assert not room.update({}, 11, coverage_ok=True)
    assert not room.update({}, 13, coverage_ok=False)
    assert not room.update({}, 14, coverage_ok=True)
    events = room.update({}, 16, coverage_ok=True)
    assert len(events) == (0 if paid else 1)
    assert not room.visits
    assert not room.update({}, 18, coverage_ok=True)


def test_floor_calibration_and_mirrored_crossing() -> None:
    from survai.config import CameraConfig, FloorCalibration, MultiCameraConfig
    from survai.floor_calibration import calibrate_geometry, project
    from survai.multi_test import geometry_selftest

    points = [(0.1, 0.1), (0.8, 0.1), (0.8, 0.8), (0.1, 0.8), (0.4, 0.37), (0.68, 0.61)]
    config = MultiCameraConfig(
        cameras=[CameraConfig(id=key, name=key, source=i) for i, key in enumerate(("a", "b"))],
        calibration=FloorCalibration(reference="a", landmarks=[
            {"name": str(i), "a": [x, y], "b": [1 - x, y]} for i, (x, y) in enumerate(points)
        ]),
    )
    matrices = calibrate_geometry(config, {"a": (640, 480), "b": (640, 480)})
    assert project([(0.3, 0.6)], matrices["a"])[0] == pytest.approx(project([(0.7, 0.6)], matrices["b"])[0])
    assert geometry_selftest()
    config.calibration.landmarks[5]["b"] = [0.2, 0.2]
    with pytest.raises(ValueError, match="calibration error"):
        calibrate_geometry(config, {"a": (640, 480), "b": (640, 480)})


def test_skew_and_confirmation_do_not_duplicate_guest() -> None:
    from survai.config import FusionConfig
    from survai.fusion import Detection, FloorTracker, fuse, synchronized_frames

    config = FusionConfig()
    tracker = FloorTracker(config)
    track = Track(4, "person", (0.2, 0.2, 0.4, 0.6))
    point = (0.3, 0.6)
    detections = {"a": [Detection("a", track, point, 1)], "b": [Detection("b", track, point, 1.3)]}
    assert len(fuse(detections, config)) == 2
    assert synchronized_frames({"a": [1, 1.5], "b": [1.04, 1.3]}, 100) == {"a": 1, "b": 1.04}
    assert not tracker.update(fuse(detections, config), 1, allow_new=False)
    for i in range(3):
        stamp = 2 + i / 10
        views = {key: [Detection(key, track, point, stamp)] for key in ("a", "b")}
        output = tracker.update(fuse(views, config), stamp)
        if i < 2:
            assert output == {}
    assert output["a"][0].id == output["b"][0].id == 1
    tracker.retire({1})
    assert tracker.next_number == 2


def test_same_facing_cropped_views_share_two_guest_numbers() -> None:
    from survai.config import FusionConfig
    from survai.fusion import Detection, FloorTracker, fuse

    config = FusionConfig(same_view_order=True)
    tracker = FloorTracker(config)
    for tick in range(6):
        now = tick / 10
        # Like the laptop and phone demo, both cameras crop out the feet.
        # The right guest is near the edge of the MacBook image and centered
        # on the phone. The projected positions do not pass the floor gate.
        mac = [
            Detection("mac", Track(10, "person", (0.18, 0.32, 0.48, 1.0)), (0.35, 1.1), now, anchor_clipped=True),
            Detection("mac", Track(20, "person", (0.60, 0.1, 1.0, 1.0)), (0.75, 1.1), now, anchor_clipped=True),
        ]
        phone = [
            Detection("phone", Track(30, "person", (0.26, 0.12, 0.62, 1.0)), (0.46, 1.37), now, anchor_clipped=True),
            Detection("phone", Track(40, "person", (0.0, 0.30, 0.22, 1.0)), (0.11, 1.37), now, anchor_clipped=True),
        ]
        if tick >= 3:
            phone.pop()  # The left guest slips out of the phone's view.
        observations = fuse({"mac": mac, "phone": phone}, config)
        output = tracker.update(observations, now)
        if tick < 2:
            assert not output
            continue
        assert {track.id for track in output["mac"]} == {1, 2}
        assert {track.id for track in output["phone"]} == ({1, 2} if tick == 2 else {2})
        assert next(track.id for track in output["phone"] if track.box == phone[0].track.box) == 2
    assert tracker.next_number == 3
