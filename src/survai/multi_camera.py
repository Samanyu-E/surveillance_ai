import logging
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from threading import Event as StopEvent, Lock, Thread
from time import monotonic

import cv2
import numpy as np

from .alerts.notify import AlertNotifier
from .apron import ApronColor, EmployeeLabels
from .capture import Capture, Frame
from .config import CameraConfig, MultiCameraConfig, WalkoutConfig
from .detectors.walkout import ExitType, VisitState, WalkoutDetector
from .doorway import Crossing, DoorCounter
from .events import Event
from .employee_marker import PhoneMarker
from .evidence import Evidence, snapshot_candidates
from .floor_calibration import calibrate_geometry, draw_floor_grid, project
from .fusion import Detection, FloorTracker, fuse, synchronized_frames
from .hud import annotate_walkout
from .identity import PersonIdentities
from .multi_identity import GlobalIdentityManager, describe
from .multi_visit import RoomVisits, Sighting
from .preview import inference_frame
from .profiles import VisitProfiles
from .server import Dashboard
from .state import CameraFeed, DoorActivity, State
from .tracks import Track
from .vision import MultiVision
from .zones import Point, crosses, inside, side

LOG = logging.getLogger(__name__)


@dataclass
class Camera:
    spec: CameraConfig
    layout: WalkoutConfig
    capture: Capture
    first: Frame
    evidence: Evidence | None
    local: PersonIdentities
    hud: WalkoutDetector
    buffer: deque[tuple[float, Frame]] = field(default_factory=lambda: deque(maxlen=8))
    buffer_lock: Lock = field(default_factory=Lock)
    previous: dict[int, Point] = field(default_factory=dict)
    crossed: set[int] = field(default_factory=set)
    tracks: list[Track] = field(default_factory=list)
    pending: list[Track] = field(default_factory=list)
    confidence: dict[int, float] = field(default_factory=dict)
    image: Frame | None = None
    overlay: Frame | None = None
    jpeg: bytes | None = None
    processed_at: float | None = None
    observed_at: float | None = None
    capture_at: float | None = None
    frame_count: int = 0
    replay_index: int = -1
    eof: bool = False
    floor_matrix: np.ndarray | None = None

    def grab(self, stop: StopEvent) -> None:
        with self.buffer_lock:
            self.buffer.append((monotonic(), self.first))
        while not stop.is_set():
            frame = self.capture.read()
            if frame is None:
                if stop.wait(0.1):
                    break
            else:
                with self.buffer_lock:
                    self.buffer.append((monotonic(), frame))

    def buffered(self) -> dict[float, Frame]:
        with self.buffer_lock:
            return {stamp: frame for stamp, frame in self.buffer
                    if self.capture_at is None or stamp > self.capture_at}

    def frame(self, now: float) -> tuple[float, Frame] | None:
        target = int(now * self.capture.fps + 1e-6)
        frame = None
        while self.replay_index < target and not self.eof:
            self.replay_index += 1
            result = self.first if self.replay_index == 0 else self.capture.read()
            if result is None:
                self.eof = True
            else:
                frame = result
        return (self.replay_index / self.capture.fps, frame) if frame is not None else None

    def sight(self, track: Track) -> Sighting:
        left, top, right, bottom = track.box
        point = track.point if self.layout.anchor == "bottom_center" else ((left + right) / 2, (top + bottom) / 2)
        exit_type: ExitType | None = None
        if self.layout.exit_band and inside(point, self.layout.exit_band, include_boundary=True):
            exit_type = "track_lost"
        line = self.layout.exit_line
        if line and side(*line, point) != 0:
            previous = self.previous.get(track.id)
            self.previous[track.id] = point
            if side(*line, point) > 0:
                self.crossed.discard(track.id)
            elif previous is not None and side(*line, previous) > 0 and crosses(previous, point, line):
                self.crossed.add(track.id)
            if track.id in self.crossed:
                exit_type = "line"
        return Sighting(self.spec.id, point, inside(point, self.layout.dining_zone),
                        inside(point, self.layout.pay_zone), exit_type, self.observed_at)

    def sightings(self) -> list[tuple[int, Sighting]]:
        sightings = [(track.id, self.sight(track)) for track in self.tracks]
        visible = {track.id for track in self.tracks}
        self.previous = {key: point for key, point in self.previous.items() if key in visible}
        self.crossed.intersection_update(visible)
        return sightings


def camera_grid(cameras: dict[str, Camera]) -> Frame:
    tiles = []
    for camera in cameras.values():
        tile = np.full((290, 480, 3), (20, 16, 12), np.uint8)
        if camera.overlay is not None:
            height, width = camera.overlay.shape[:2]
            scale = min(480 / width, 260 / height)
            fitted = cv2.resize(camera.overlay, (round(width * scale), round(height * scale)))
            h, w = fitted.shape[:2]
            tile[30:30 + h, (480 - w) // 2:(480 - w) // 2 + w] = fitted
        cv2.putText(tile, camera.spec.name, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (224, 240, 249), 1)
        tiles.append(tile)
    if len(tiles) == 1:
        return tiles[0]
    if len(tiles) == 2:
        return np.hstack(tiles)
    if len(tiles) == 3:
        tiles.append(np.zeros_like(tiles[0]))
    return np.vstack((np.hstack(tiles[:2]), np.hstack(tiles[2:])))


def run_multi(
    config: MultiCameraConfig, defaults: WalkoutConfig, *, weights: str | None, output: Path,
    dashboard_port: int | None, allow_fallback: bool, headless: bool, grid: bool, whatsapp: bool,
    record: Path | None = None, floor_grid: bool = False, whatsapp_path: Path | None = None,
    state: State | None = None, stop: StopEvent | None = None, mark_paid: StopEvent | None = None,
) -> int:
    managed = stop is not None
    stop = stop if stop is not None else StopEvent()
    with ExitStack() as resources:
        state = state if state is not None else State()
        if dashboard_port is not None:
            dashboard = Dashboard(output, dashboard_port, allow_fallback=allow_fallback)
            state = resources.enter_context(dashboard)
            LOG.info("Dashboard at http://127.0.0.1:%d", dashboard.port)
        cameras = {}
        now = 0.0
        for spec in config.active_cameras:
            if stop.is_set():
                return 0
            layout = spec.layout(defaults) if spec.walkout_enabled else defaults
            LOG.info("Opening %s (source %s)", spec.name, spec.source)
            try:
                capture = Capture(str(spec.source), native_resolution=True)
                resources.callback(capture.close)
                first = capture.first_frame()
            except (RuntimeError, OSError) as exc:
                raise RuntimeError(f"{spec.name}: {exc} Check the configured camera source and permissions.") from exc
            evidence = None
            if spec.walkout_enabled:
                evidence = Evidence(output, state, defaults.evidence_retention_days)
                resources.callback(lambda evidence=evidence: evidence.close(now))
            camera = Camera(spec, layout, capture, first, evidence,
                            PersonIdentities(defaults.lost_grace_seconds, defaults.reassociation_distance,
                                             defaults.occlusion_seconds), WalkoutDetector(layout))
            if spec.floor_points is not None:
                camera.floor_matrix = cv2.getPerspectiveTransform(
                    np.float32(spec.floor_points), np.float32([(0, 0), (1, 0), (1, 1), (0, 1)]),
                )
                if abs(np.linalg.det(camera.floor_matrix)) < 1e-9:
                    raise ValueError(f"Invalid floor points for {spec.id}")
            cameras[spec.id] = camera
        walkout_cameras = {key for key, cam in cameras.items() if cam.spec.walkout_enabled}
        state.publish_cameras({key: CameraFeed(cam.spec.name, "Starting") for key, cam in cameras.items()})
        with state.lock:
            state.walkout_enabled = bool(walkout_cameras)
        live = next(iter(cameras.values())).capture.live
        if any(cam.capture.live != live for cam in cameras.values()):
            raise ValueError("Use all live cameras or all recordings")
        calibrated = config.calibration is not None and len(cameras) > 1 and len(cameras) == len(config.cameras)
        coverage: dict[str, object] = {}
        if calibrated:
            matrices = calibrate_geometry(config, {
                key: (cam.first.shape[1], cam.first.shape[0]) for key, cam in cameras.items()
            })
            for key, cam in cameras.items():
                cam.floor_matrix = matrices[key]
                coverage[key] = {
                    "name": cam.spec.name,
                    "outline": project([(0.02, 0.1), (0.98, 0.1), (0.98, 0.98), (0.02, 0.98)], matrices[key]),
                }
        # Counting uses saved geometry even when the door overlay is hidden.
        doorway = next((cam for cam in cameras.values() if cam.spec.door_line is not None), None)
        door = DoorCounter(doorway.spec.door_line) if doorway is not None and doorway.spec.door_line else None
        if door is not None:
            state.publish_door_counts(0, 0)
        aprons = {key: ApronColor(cam.spec.apron_color) for key, cam in cameras.items() if cam.spec.apron_color}
        markers = {key: PhoneMarker() for key, cam in cameras.items() if cam.spec.employee_marker}
        employees = EmployeeLabels()
        marker_employees = EmployeeLabels(sticky=True)
        floor_tracker = FloorTracker(config.fusion) if calibrated else None
        writers = {}
        if record is not None:
            if not live:
                raise ValueError("Multi-camera recording requires live cameras")
            destinations = {key: record.with_name(f"{record.stem}-{key}.mp4") for key in cameras}
            if any(path.exists() for path in destinations.values()):
                raise ValueError("A multi-camera recording already exists at that path")
            record.parent.mkdir(parents=True, exist_ok=True)
            for key, cam in cameras.items():
                height, width = cam.first.shape[:2]
                writer = cv2.VideoWriter(str(destinations[key]), cv2.VideoWriter_fourcc(*"mp4v"),
                                         config.fusion.tick_hz if calibrated else config.target_fps, (width, height))
                resources.callback(writer.release)
                if not writer.isOpened():
                    raise RuntimeError(f"Could not record {key}")
                writers[key] = writer
        if stop.is_set():
            return 0
        vision = MultiVision(list(cameras), weights=weights, image_size=config.image_size)
        identities = GlobalIdentityManager(config.identity)
        visits = RoomVisits(defaults)
        profiles = VisitProfiles()
        notifier = AlertNotifier(whatsapp, native_sound=dashboard_port is None and not managed,
                                 settings_path=whatsapp_path)
        resources.callback(notifier.close)
        threads = [Thread(target=cam.grab, args=(stop,), daemon=True) for cam in cameras.values()] if live else []
        for thread in threads:
            thread.start()

        def stop_capture() -> None:
            stop.set()
            for thread in threads:
                thread.join(timeout=None if managed else 1)

        resources.callback(stop_capture)
        window = "Walk-out cameras"
        show = not headless
        if show:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            resources.callback(cv2.destroyWindow, window)
        started = monotonic()
        tick = 0
        interval = 1 / (config.fusion.tick_hz if calibrated else config.target_fps)
        flash_until = -1.0
        alert_title = "POSSIBLE WALK-OUT"
        finished = False
        stalled_after = min(0.5, defaults.lost_grace_seconds)
        while not stop.is_set():
            now = monotonic() - started if live else tick * interval
            tick += 1
            if finished:
                arrivals = {}
            elif live:
                buffers = {key: frames for key, cam in cameras.items() if (frames := cam.buffered())}
                selected = synchronized_frames({key: list(frames) for key, frames in buffers.items()},
                                               config.fusion.max_skew_ms)
                arrivals = {key: (stamp, buffers[key][stamp]) for key, stamp in selected.items()}
            else:
                arrivals = {key: cam.frame(now) for key, cam in cameras.items()}
            if writers and len(arrivals) == len(cameras) and all(value is not None for value in arrivals.values()):
                for key, captured in arrivals.items():
                    assert captured is not None
                    writers[key].write(captured[1])
            updated = {}
            for key, captured in arrivals.items():
                if captured is None:
                    continue
                stamp, image = captured
                cam = cameras[key]
                cam.capture_at = stamp
                cam.processed_at = max(0, stamp - started) if live else stamp
                cam.observed_at = now
                cam.image = cv2.flip(image, 1) if cam.spec.mirror else image
                updated[key] = inference_frame(cam.image)
            if not finished and not live and any(cam.eof for cam in cameras.values()):
                finished = True
                for cam in cameras.values():
                    if cam.evidence is not None:
                        cam.evidence.finish_pending(now)
                profiles.clear()
                state.publish_profiles(profiles.active)
                if floor_tracker is not None:
                    state.publish_floor([], coverage)
                state.publish_cameras({
                    key: CameraFeed(cam.spec.name, "Replay complete",
                                    round(cam.frame_count / max(now, interval), 1), cam.jpeg)
                    for key, cam in cameras.items()
                })
                with state.lock:
                    state.status = "Replay complete"
                    state.alert_active = False
                if dashboard_port is None and not managed:
                    break
            if finished:
                if not show:
                    stop.wait(0.1)
                    continue
            else:
                detections = vision.batch(updated) if updated else {}
                projected = {}
                crossings: list[Crossing] = []
                door_boxes = {}
                for key, detected in detections.items():
                    cam = cameras[key]
                    assert cam.image is not None
                    local = cam.local.update(updated[key], detected, now)
                    cam.frame_count += 1
                    if door is not None and cam is doorway:
                        crossings = door.update_tracks(local, cam.local.confirmed_paths, cam.hud._anchor, now)
                        if crossings:
                            door_boxes = {track.id: track.box for track in local}
                    if len(cameras) == 1:
                        cam.tracks = local
                        continue
                    descriptors = {}
                    if (cam.frame_count - 1) % config.identity.sample_every == 0:
                        for track in local:
                            descriptor = describe(cam.image, track, local, config.identity)
                            if descriptor is not None:
                                descriptors[track.id] = descriptor
                    floor = {}
                    if floor_tracker is not None:
                        assert cam.floor_matrix is not None and cam.processed_at is not None
                        anchors = [((track.box[0] + track.box[2]) / 2,
                                    track.box[3] if config.fusion.plane == "floor" else track.box[1])
                                   for track in local]
                        projected[key] = [
                            Detection(
                                key, track, point, cam.processed_at, descriptors.get(track.id),
                                anchor_clipped=track.box[3] >= 0.98 if config.fusion.plane == "floor"
                                else track.box[1] <= 0.02,
                            )
                            for track, point in zip(local, project(anchors, cam.floor_matrix), strict=True)
                        ]
                    else:
                        cam.tracks, cam.pending, cam.confidence = identities.update(key, local, descriptors, now, floor)
                if floor_tracker is not None and updated:
                    stamps = [cameras[key].processed_at for key in updated]
                    newest = max(stamp for stamp in stamps if stamp is not None)
                    oldest = min(stamp for stamp in stamps if stamp is not None)
                    synchronized = len(updated) == len(cameras) and newest - oldest <= config.fusion.max_skew_ms / 1000
                    global_tracks = floor_tracker.update(
                        fuse(projected, config.fusion), now, allow_new=synchronized,
                    )
                    for key in updated:
                        cameras[key].tracks = global_tracks.get(key, [])

                observations: dict[int, bool] = {}
                for key in aprons.keys() & updated.keys():
                    for track in cameras[key].tracks:
                        match = aprons[key].matches(updated[key], track)
                        if match is not None:
                            observations[track.id] = observations.get(track.id, False) or match
                employees.update(observations, now)
                marker_results = {}
                for key in markers.keys() & updated.keys():
                    image = cameras[key].image
                    assert image is not None
                    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
                    for track in cameras[key].tracks:
                        matched = markers[key].matches(hsv, track)
                        marker_results[track.id] = marker_results.get(track.id, False) or matched
                marker_employees.update(marker_results, now)
                employee_ids = employees.employees | marker_employees.employees

                fresh = {key: cam for key, cam in cameras.items() if cam.processed_at is not None
                         and now - cam.processed_at <= stalled_after}
                sightings: dict[int, list[Sighting]] = {}
                visible_cameras: dict[int, set[str]] = {}
                for key, cam in fresh.items():
                    for track in cam.tracks:
                        visible_cameras.setdefault(track.id, set()).add(key)
                    if not walkout_cameras:
                        for track in cam.tracks:
                            sightings.setdefault(track.id, []).append(Sighting(key, track.point))
                    if key not in walkout_cameras:
                        continue
                    for guest, sighting in cam.sightings():
                        sightings.setdefault(guest, []).append(sighting)
                previous_guests = visits.visits.copy()
                coverage_ok = walkout_cameras <= fresh.keys() and not any(
                    cameras[key].pending for key in walkout_cameras
                )
                if floor_tracker is not None and coverage_ok and len(walkout_cameras) > 1:
                    stamps = [fresh[key].processed_at for key in walkout_cameras]
                    coverage_ok &= max(stamps) - min(stamps) <= config.fusion.max_skew_ms / 1000
                if mark_paid is not None and mark_paid.is_set():
                    mark_paid.clear()
                    visits.mark_paid(now)
                for guest_id in state.take_payments():
                    if guest_id not in employee_ids:
                        visits.mark_guest_paid(guest_id, now)
                triggered = visits.update(sightings, now, coverage_ok=coverage_ok, employee_ids=employee_ids)
                identities.retire(previous_guests.keys() - visits.visits.keys() - visible_cameras.keys())
                if floor_tracker is not None:
                    ended = {event.track_id for _, event in triggered if event.event_type == "possible_walkout"} | {
                        key for key in previous_guests.keys() - visits.visits.keys()
                        if previous_guests[key].state == VisitState.PAID
                    }
                    floor_tracker.retire(ended - visible_cameras.keys())
                    state.publish_floor([
                        {"id": guest.number, "trail": guest.trail, "visible": guest.number in visible_cameras,
                         "present": now - guest.last_seen <= config.fusion.lost_grace_seconds,
                         "cameras": sorted(visible_cameras.get(guest.number, set()))}
                        for guest in floor_tracker.guests if guest.number is not None
                    ], coverage)
                events: list[Event] = []
                for key, event in triggered:
                    evidence = cameras[key].evidence
                    assert evidence is not None
                    events.append(evidence.register(event))
                if events:
                    flash_until = now + 2.5
                    alert_title = "NEW CUSTOMER AT COUNTER" if events[-1].type == "counter_wait" else "POSSIBLE WALK-OUT"
                timestamp = datetime.now().astimezone()
                if door is not None and doorway is not None:
                    displayed_ids = {track.box: track.id for track in doorway.tracks} if crossings else {}
                    employee_count = len(employee_ids & visible_cameras.keys())
                    activity = []
                    for crossing in crossings:
                        displayed_id = displayed_ids.get(door_boxes[crossing.track_id])
                        item = replace(crossing, track_id=displayed_id) if displayed_id is not None else crossing
                        activity.append(DoorActivity(item, timestamp, displayed_id in employee_ids, employee_count))
                    state.publish_door_counts(door.came_in, door.left, employee_count, activity)
                visible = [track for cam in fresh.values() for track in cam.tracks]
                profiles.update(visible, visits.visits, timestamp)
                for key, profile in profiles.active.items():
                    profile.cameras = tuple(camera for camera in fresh if camera in visible_cameras.get(key, set()))
                    profile.dwell_seconds = visits.visits[key].dining_seconds
                    confidences = [cam.confidence[key] for cam in fresh.values() if key in cam.confidence]
                    profile.match_confidence = min(confidences, default=None)
                state.publish_profiles(profiles.active)

                feeds = {}
                for key, cam in cameras.items():
                    fps = cam.frame_count / max(monotonic() - started, 0.001)
                    if key in updated:
                        assert cam.image is not None
                        if cam.evidence is not None:
                            cam.hud.visits = {
                                track.id: replace(visits.visits[track.id], point=cam.hud._anchor(track))
                                for track in cam.tracks
                            }
                            cam.overlay = annotate_walkout(updated[key].copy(), cam.tracks, cam.hud, fps,
                                                           now < flash_until, displayed_at=timestamp, global_labels=True,
                                                           door_line=cam.spec.visible_door_line, employees=employee_ids,
                                                           alert_title=alert_title)
                            if floor_grid and calibrated and cam.floor_matrix is not None:
                                draw_floor_grid(cam.overlay, cam.floor_matrix)
                            if cam.evidence.wants_frame(now):
                                candidates = snapshot_candidates(cam.image, cam.tracks, now)
                                cam.evidence.observe(cam.overlay, now, snapshots=candidates)
                        else:
                            cam.overlay = annotate_walkout(
                                updated[key].copy(), cam.tracks, None, fps, False,
                                displayed_at=timestamp, global_labels=True,
                                door_line=cam.spec.visible_door_line, employees=employee_ids,
                                customer_labels=not walkout_cameras, door_anchor=cam.layout.anchor,
                            )
                        ok, jpeg = cv2.imencode(".jpg", cam.overlay)
                        cam.jpeg = jpeg.tobytes() if ok else None
                    if cam.evidence is not None:
                        cam.evidence.advance(now)
                    feeds[key] = CameraFeed(cam.spec.name, "Live" if key in fresh else "Waiting for frames",
                                            round(fps, 1), cam.jpeg)
                state.publish_cameras(feeds)
                if updated or events:
                    frame = camera_grid(cameras)
                    ok, jpeg = cv2.imencode(".jpg", frame)
                    state.publish(jpeg.tobytes() if ok else b"", events, now, len(visible_cameras), 0, now < flash_until)
                    if show:
                        preview = frame if grid else next(iter(cameras.values())).overlay
                        cv2.imshow(window, frame if preview is None else preview)
                for event in events:
                    notifier.notify(event)
            if show:
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) == 0:
                    break
                if key == ord("p"):
                    visits.mark_paid(now)
                if key == ord("g"):
                    floor_grid = not floor_grid
            deadline = started + tick * interval
            if live and deadline < monotonic() - interval:
                tick = int((monotonic() - started) / interval)
                deadline = started + (tick + 1) * interval
            if live or dashboard_port is not None or show or managed:
                stop.wait(max(0, deadline - monotonic()))
        for key, cam in cameras.items():
            LOG.info("%s processed %d frames", key, cam.frame_count)
        return 0
