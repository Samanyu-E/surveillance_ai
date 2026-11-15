import logging

import numpy as np

from .config import IdentityConfig, WalkoutConfig
from .multi_identity import GlobalIdentityManager, describe
from .multi_visit import RoomVisits, Sighting
from .tracks import Track

LOG = logging.getLogger(__name__)


def selftest_multi(config: WalkoutConfig, *, evaluate: bool = False) -> bool:
    settings = IdentityConfig(enroll_frames=3, min_box_height_px=50)
    manager = GlobalIdentityManager(settings)
    labeled: dict[str, set[int]] = {"A": set(), "B": set()}
    assignments = 0
    for camera_index, camera in enumerate(("cam1", "cam2")):
        frame = np.full((240, 320, 3), 70, np.uint8)
        tracks = [
            Track(camera_index * 10 + 1, "person", (0.12, 0.12, 0.32, 0.82)),
            Track(camera_index * 10 + 2, "person", (0.62, 0.12, 0.82, 0.82)),
        ]
        if camera_index:
            tracks.reverse()
        descriptors = {}
        for track, color in zip(tracks, ((40, 40, 220), (220, 40, 40)), strict=True):
            left, top, right, bottom = track.box
            frame[round(top * 240):round(bottom * 240), round(left * 320):round(right * 320)] = (
                np.array(color) * (0.68 if camera_index else 1)
            ).astype(np.uint8)
            descriptor = describe(frame, track, tracks, settings)
            if descriptor is not None:
                descriptors[track.id] = descriptor
        for tick in range(3):
            confirmed, _, _ = manager.update(camera, tracks, descriptors, (camera_index * 3 + tick) / 10, {})
        assignments += len(confirmed)
        for label, track in zip(labeled, confirmed):
            labeled[label].add(track.id)
    merges = len(labeled["A"] & labeled["B"])
    splits = sum(max(0, len(ids) - 1) for ids in labeled.values())
    matched = assignments == 4 and merges == splits == 0 and all(labeled.values())
    LOG.info("%s cross-camera matching (%d/4 assigned, %d merges, %d splits)",
             "PASS" if matched else "FAIL", assignments, merges, splits)
    if evaluate:
        return bool(matched)

    room = RoomVisits(config)
    guests = {1: [Sighting("cam1", (0.4, 0.5), dining=True), Sighting("cam2", (0.5, 0.5), dining=True)]}
    room.update(guests, 0, coverage_ok=True)
    room.update(guests, config.min_dwell_seconds, coverage_ok=True)
    dwell_ok = room.visits[1].dining_seconds == config.min_dwell_seconds
    now = config.min_dwell_seconds + 1
    exits = {1: [Sighting("cam2", (0.94, 0.5), exit="track_lost")]}
    one_view = room.update(exits, now, coverage_ok=True)
    absent = room.update({}, now + 1, coverage_ok=True)
    events = room.update({}, now + 1 + config.lost_grace_seconds + 0.01, coverage_ok=True)
    alerts_ok = not one_view and not absent and len(events) == 1 and events[0][0] == "cam2"
    LOG.info("%s one visit timeline and one unpaid exit", "PASS" if dwell_ok and alerts_ok else "FAIL")
    return bool(matched and dwell_ok and alerts_ok)


def geometry_selftest() -> bool:
    from .config import FusionConfig
    from .floor_calibration import project
    from .fusion import Detection, FloorTracker, fuse

    config = FusionConfig(max_floor_dist=0.12)
    perspective = {
        "cam1": np.float32([[0.75, 0.08, 0.04], [0, 0.72, 0.10], [0.16, 0.1, 1]]),
        "cam2": np.float32([[-0.62, 0.03, 0.9], [0.04, 0.66, 0.13], [-0.09, 0.11, 1]]),
    }
    outfit = np.ones((2, 16, 8), dtype=np.float32) / 128
    tracker = FloorTracker(config)
    previous: dict[int, int] = {}
    switches = misses = duplicates = count_error = 0
    for tick in range(33):
        now = tick / 10
        truth = {
            1: (0.18 + tick * 0.017, 0.40),
            2: (0.82 - tick * 0.017, 0.49),
        }
        if tick >= 12:
            truth[3] = (0.48, 0.76)
        views = {}
        for camera, matrix in perspective.items():
            detections = []
            for number, position in truth.items():
                if camera == "cam1" and number == 1 and 14 <= tick <= 21:
                    continue
                pixel = project([position], matrix)[0]
                track = Track(number + 10 * (camera == "cam2"), "person",
                              (pixel[0] - .03, pixel[1] - .18, pixel[0] + .03, pixel[1]))
                corrected = project([pixel], np.linalg.inv(matrix))[0]
                detections.append(Detection(camera, track, corrected, now, outfit))
            views[camera] = detections
        observations = fuse(views, config)
        output = tracker.update(observations, now)
        active = {track.id for tracks in output.values() for track in tracks}
        expected = len(truth) - int(12 <= tick < 14) if tick >= 2 else 0
        count_error += abs(len(active) - expected)
        duplicates += max(0, len(active) - expected)
        misses += sum(len(group.detections) == 1 for group in observations) - int(14 <= tick <= 21)
        for camera, tracks in output.items():
            labels = {detection.track.box: detection.track.id % 10 for detection in views[camera]}
            for track in tracks:
                label = labels[track.box]
                switches += int(label in previous and previous[label] != track.id)
                previous[label] = track.id
    passed = switches == misses == duplicates == count_error == 0
    LOG.info("%s geometry: %d ID switches, %d missed associations, %d duplicate guests, %d count error",
             "PASS" if passed else "FAIL", switches, misses, duplicates, count_error)
    return passed
