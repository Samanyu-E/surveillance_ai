import logging
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep

import cv2

from .alerts.notify import AlertNotifier
from .capture import Capture, Frame
from .config import WalkoutConfig
from .detectors.walkout import WalkoutDetector
from .evidence import Evidence, snapshot_candidates
from .hud import annotate_walkout
from .identity import PersonIdentities
from .profiles import VisitProfiles
from .server import Dashboard
from .state import State
from .vision import Vision

LOG = logging.getLogger(__name__)


def inference_frame(frame: Frame) -> Frame:
    height, width = frame.shape[:2]
    if width <= 640:
        return frame
    return cv2.resize(frame, (640, max(1, round(height * 640 / width))), interpolation=cv2.INTER_AREA)


def list_cameras(limit: int = 6) -> int:
    found = 0
    for index in range(limit):
        capture = None
        try:
            capture = Capture(str(index), native_resolution=True)
            frame = capture.first_frame()
            height, width = frame.shape[:2]
            LOG.info("Camera %d: %dx%d", index, width, height)
            found += 1
        except RuntimeError:
            continue
        finally:
            if capture is not None:
                capture.close()
    if not found:
        LOG.error("No usable cameras. On macOS, check System Settings > Privacy & Security > Camera.")
    return 0 if found else 1


def run_preview(
    config: WalkoutConfig, source: str, weights: str | None, record: Path | None, headless: bool,
    *, output: Path = Path("alerts"), dashboard_port: int | None = None,
    dashboard_port_fallback: bool = False, whatsapp: bool = False,
) -> int:
    if record is not None:
        if not source.isdecimal():
            raise RuntimeError("--record requires a live camera")
        if record.exists():
            raise RuntimeError(f"Recording already exists: {record}")
        if record.suffix.lower() != ".mp4":
            raise RuntimeError("--record requires an .mp4 output")

    capture = Capture(source, native_resolution=True)
    writer = None
    window = "Walk-out demo"
    window_open = False
    resources = ExitStack()
    last_seconds = 0.0
    try:
        raw = capture.first_frame()
        vision = Vision(weights=weights)
        identities = PersonIdentities(
            config.lost_grace_seconds, config.reassociation_distance, config.occlusion_seconds,
        )
        detector = WalkoutDetector(config)
        profiles = VisitProfiles()
        state = State()
        if dashboard_port is not None:
            dashboard = Dashboard(output, dashboard_port, allow_fallback=dashboard_port_fallback)
            state = resources.enter_context(dashboard)
            if dashboard.port != dashboard_port:
                LOG.info("Port %d is busy. Using port %d instead.", dashboard_port, dashboard.port)
            LOG.info("Dashboard at http://127.0.0.1:%d", dashboard.port)
        evidence = Evidence(output, state, config.evidence_retention_days)
        resources.callback(lambda: evidence.close(last_seconds))
        notifier = AlertNotifier(whatsapp, native_sound=dashboard_port is None)
        resources.callback(notifier.close)
        height, width = raw.shape[:2]
        if record is not None:
            record.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(record), cv2.VideoWriter_fourcc(*"mp4v"), capture.fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError(f"Could not open recording: {record}")
        if not headless:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            window_open = True
            cv2.resizeWindow(window, min(960, width), round(height * min(960, width) / width))

        LOG.info("Source %s at %dx%d. Tracking device: %s.", source, width, height, vision.device)
        started = monotonic()
        frame_count = 0
        recorded_frames = 0
        previous_raw = raw
        flash_until = -1.0
        alert_title = "POSSIBLE WALK-OUT"
        while raw is not None:
            frame_time = monotonic() - started
            if writer is not None:
                # Keep replay dwell timers aligned with the original capture clock.
                frame_index = round(frame_time * capture.fps)
                while recorded_frames < frame_index:
                    writer.write(previous_raw)
                    recorded_frames += 1
                if recorded_frames == frame_index:
                    writer.write(raw)
                    recorded_frames += 1
                previous_raw = raw
            mirrored = cv2.flip(raw, 1)
            frame = inference_frame(mirrored)
            raw_tracks = vision.track(frame)
            if vision.reset_tracks:
                # The CPU tracker starts its own numbering.
                identities.forget_tracker_ids()
                vision.reset_tracks = False
            now = frame_time if capture.live else frame_count / capture.fps
            tracks = identities.update(frame, raw_tracks, now)
            last_seconds = now
            frame_count += 1
            for guest_id in state.take_payments():
                detector.mark_guest_paid(guest_id, now)
            triggered = detector.update(tracks, now, occluded_ids=identities.hidden_ids)
            if triggered:
                flash_until = now + 2.5
                alert_title = "NEW CUSTOMER AT COUNTER" if triggered[-1].event_type == "counter_wait" else "POSSIBLE WALK-OUT"
            flash = now < flash_until
            displayed_at = datetime.now().astimezone()
            if dashboard_port is not None:
                profiles.update(tracks, detector.visits, displayed_at)
                state.publish_profiles(profiles.active)
            if evidence.wants_frame(now):
                snapshots = snapshot_candidates(mirrored, tracks, now)
                evidence.observe(annotate_walkout(
                    mirrored.copy(), tracks, detector, vision.fps, flash, displayed_at=displayed_at,
                    alert_title=alert_title,
                ), now, snapshots=snapshots)
            events = [evidence.register(event) for event in triggered]
            if window_open or dashboard_port is not None:
                annotate_walkout(frame, tracks, detector, vision.fps, flash, displayed_at=displayed_at,
                                 alert_title=alert_title)
            if dashboard_port is not None or events:
                jpeg = b""
                if dashboard_port is not None:
                    ok, encoded = cv2.imencode(".jpg", frame)
                    if ok:
                        jpeg = encoded.tobytes()
                state.publish(jpeg, events, now, len(tracks), 0, flash)
            for event in events:
                notifier.notify(event)
            evidence.advance(now)
            if window_open:
                cv2.imshow(window, frame)
                delay = 1
                if not capture.live:
                    delay = max(1, round(1000 * (frame_count / capture.fps - (monotonic() - started))))
                key = cv2.waitKey(delay) & 0xFF
                if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) == 0:
                    break
                if key == ord("p"):
                    detector.mark_dining_zone_paid(now)
                if key == ord("r"):
                    detector.reset()
                    profiles.clear()
                    state.publish_profiles(profiles.active)
                    flash_until = -1.0
            elif dashboard_port is not None and not capture.live:
                sleep(max(0, frame_count / capture.fps - (monotonic() - started)))
            raw = capture.read()
            if raw is None and capture.live:
                raise RuntimeError("Camera stopped delivering frames")
        LOG.info("Processed %d frames at %.1f FPS.", frame_count, vision.fps)
        if raw is None and dashboard_port is not None:
            profiles.clear()
            state.publish_profiles(profiles.active)
            evidence.finish_pending(last_seconds)
            with state.lock:
                state.status = "Replay complete"
                state.alert_active = False
            LOG.info("Replay complete. Press Ctrl+C to close the dashboard.")
            while True:
                evidence.check_retention()
                if window_open:
                    key = cv2.waitKey(50) & 0xFF
                    if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) == 0:
                        break
                else:
                    sleep(0.25)
        return 0
    finally:
        if writer is not None:
            writer.release()
        capture.close()
        if window_open:
            cv2.destroyAllWindows()
        resources.close()
