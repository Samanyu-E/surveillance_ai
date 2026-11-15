import logging
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import cv2
import numpy as np
import yaml

from .capture import Capture, Frame
from .config import CameraConfig, FloorCalibration, MultiCameraConfig
from .zones import Point

LOG = logging.getLogger(__name__)
CAMERA_WARMUP_SECONDS = 2
STALE_FRAME_SECONDS = 1


class CalibrationFeed:
    """Keep a live camera reading while the calibration window handles input."""

    def __init__(self, capture: Capture, mirror: bool) -> None:
        self.live = capture.live
        self.first_frame_at: float | None = None
        self.latest: tuple[float, Frame] | None = None
        self.stop = Event()
        self.thread: Thread | None = None
        if capture.live:
            self.thread = Thread(target=self._read, args=(capture, mirror), daemon=True)
            self.thread.start()
        else:
            try:
                frame = capture.first_frame()
                self.latest = (monotonic(), cv2.flip(frame, 1) if mirror else frame)
            finally:
                capture.close()

    def _read(self, capture: Capture, mirror: bool) -> None:
        try:
            while not self.stop.is_set():
                frame = capture.read()
                if frame is None or not frame.size:
                    self.stop.wait(0.05)
                    continue
                stamp = monotonic()
                if self.first_frame_at is None:
                    self.first_frame_at = stamp
                self.latest = (stamp, cv2.flip(frame, 1) if mirror else frame)
        except (OSError, RuntimeError, cv2.error):
            LOG.exception("Calibration camera stopped reading frames")
        finally:
            capture.close()

    def close(self, *, wait: bool = False) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=None if wait else 1)


def project(points: list[Point], matrix: np.ndarray) -> list[Point]:
    if not points:
        return []
    result = cv2.perspectiveTransform(np.float32([points]), matrix)[0]
    return [(float(x), float(y)) for x, y in result]


def matrix_for(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    matrix, mask = cv2.findHomography(source, target, cv2.RANSAC if len(source) >= 6 else 0, 0.025)
    if matrix is None or abs(np.linalg.det(matrix)) < 1e-10 or mask is None or mask.sum() < 4:
        raise ValueError("Calibration points are degenerate. Pick at least four widely spaced landmarks.")
    return matrix


def calibrate_geometry(
    config: MultiCameraConfig, dimensions: Mapping[str, tuple[int, int]],
) -> dict[str, np.ndarray]:
    layout = config.calibration
    if layout is None or len(layout.landmarks) < 4:
        raise ValueError("Add at least four shared landmarks with --calibrate-floor.")
    ids = [camera.id for camera in config.cameras]
    reference = layout.reference or ids[0]
    if reference not in ids:
        raise ValueError("Calibration reference must name a configured camera.")

    def coordinates(name: str) -> np.ndarray:
        try:
            points = np.asarray([item[name] for item in layout.landmarks], dtype=np.float32)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Every landmark needs an [x, y] pair for {name}.") from exc
        if points.shape != (len(layout.landmarks), 2) or not np.isfinite(points).all():
            raise ValueError(f"Invalid landmark coordinates for {name}.")
        return points

    sources = {}
    for camera in ids:
        points = coordinates(camera)
        if not np.all((points >= 0) & (points <= 1)):
            points = points / np.float32(dimensions[camera])
        if not np.all((points >= 0) & (points <= 1)):
            raise ValueError(f"Landmarks for {camera} are outside its frame.")
        if cv2.contourArea(cv2.convexHull(points.astype(np.float32))) < 0.003:
            raise ValueError(f"Landmarks for {camera} are collinear or too close together.")
        sources[camera] = points

    measured = sum("floor" in item for item in layout.landmarks)
    if measured and measured != len(layout.landmarks):
        raise ValueError("Give floor coordinates to every landmark or omit them all.")
    target = coordinates("floor") if measured else sources[reference]
    scale = max(float(np.ptp(target, axis=0).max()), 0.001)
    target = (target - target.min(axis=0)) / scale
    if cv2.contourArea(cv2.convexHull(target)) < 0.003:
        raise ValueError("Calibration floor coordinates are collinear.")
    matrices = {}
    for camera, points in sources.items():
        matrix = matrix_for(points, target)
        projected = np.asarray(project([tuple(point) for point in points], matrix))
        errors = np.linalg.norm(projected - target, axis=1)
        if len(points) >= 5:
            for index in range(len(points)):
                keep = np.arange(len(points)) != index
                try:
                    trial = matrix_for(points[keep], target[keep])
                    guess = project([tuple(points[index])], trial)[0]
                    errors[index] = np.linalg.norm(np.asarray(guess) - target[index])
                except ValueError:
                    errors[index] = np.inf
        LOG.info("%s %s error: %s", camera, "leave-one-out" if len(points) >= 5 else "fit",
                 ", ".join(f"{item.get('name', i + 1)}={error:.3f}"
                           for i, (item, error) in enumerate(zip(layout.landmarks, errors, strict=True))))
        if max(errors) > layout.max_error:
            raise ValueError(f"{camera} calibration error is too high. Reclick corresponding points with --calibrate-floor.")
        matrices[camera] = matrix
    return matrices


def draw_floor_grid(frame: Frame, matrix: np.ndarray) -> None:
    inverse = np.linalg.inv(matrix)
    height, width = frame.shape[:2]

    def pixel(point: Point) -> tuple[int, int]:
        return round(np.clip(point[0], -5, 5) * width), round(np.clip(point[1], -5, 5) * height)

    for step in np.linspace(0, 1, 11):
        for line in ([(step, 0), (step, 1)], [(0, step), (1, step)]):
            a, b = project(line, inverse)
            cv2.line(frame, pixel(a), pixel(b), (180, 176, 92), 1, cv2.LINE_AA)


def save_landmarks(path: Path, landmarks: list[dict[str, object]]) -> None:
    # Replace only the YAML sequence so zone settings and surrounding comments survive.
    original = path.read_text()
    root = yaml.compose(original)
    calibration = next((value for key, value in root.value if key.value == "calibration"), None)
    if calibration is None:
        updated = original.rstrip() + "\n" + yaml.safe_dump({"calibration": {"landmarks": landmarks}}, sort_keys=False)
    else:
        node = next((value for key, value in calibration.value if key.value == "landmarks"), None)
        if node is None:
            sequence = yaml.safe_dump({"landmarks": landmarks}, default_flow_style=True, width=1000000).strip()
            if calibration.flow_style:
                index = calibration.end_mark.index - 1
                separator = ", " if calibration.value else ""
                updated = original[:index] + separator + sequence[1:-1] + original[index:]
            else:
                index = original.rfind("\n", 0, calibration.start_mark.index) + 1
                indent = " " * calibration.start_mark.column
                updated = original[:index] + indent + sequence[1:-1] + "\n" + original[index:]
        else:
            start = node.start_mark.index
            end = node.end_mark.index
            sequence = yaml.safe_dump(landmarks, default_flow_style=True, sort_keys=False, width=1000000).strip()
            if not node.flow_style:
                end = node.value[-1].end_mark.index
                key_start = original.rfind("landmarks:", 0, start)
                comments = [line for line in original[key_start:end].splitlines() if line.lstrip().startswith("#")]
                updated = original[:key_start] + "landmarks: " + sequence + "\n"
                updated += "\n".join(comments) + ("\n" if comments else "") + original[end:].lstrip("\n")
            else:
                updated = original[:start] + sequence + original[end:]
    MultiCameraConfig.model_validate(yaml.safe_load(updated))
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(updated)
    temporary.replace(path)


def floor_tool(
    config: MultiCameraConfig, path: Path, *, check: bool, headless: bool = False, doctor: bool = False,
) -> int:
    if not headless:
        return live_floor_tool(config, path, check=check)
    frames = {}
    for camera in config.cameras:
        capture = open_calibration_capture(camera, path)
        try:
            frame = capture.first_frame()
            frames[camera.id] = frame
            if doctor:
                start = monotonic()
                count = sum(capture.read() is not None for _ in range(12))
                if not count:
                    raise RuntimeError(f"{camera.name} stopped delivering frames")
                LOG.info("%s: %dx%d, %.1f FPS capture", camera.name, frame.shape[1],
                         frame.shape[0], count / max(monotonic() - start, 0.001))
        finally:
            capture.close()
    dimensions = {camera: (frame.shape[1], frame.shape[0]) for camera, frame in frames.items()}
    if check:
        calibrate_geometry(config, dimensions)
    return 0


def open_calibration_capture(camera: CameraConfig, path: Path) -> Capture:
    source = str(camera.source)
    if not source.isdecimal():
        source = str((path.parent / source).resolve())
    try:
        return Capture(source, native_resolution=True)
    except RuntimeError as exc:
        raise RuntimeError(f"{camera.name}: {exc}") from exc


def live_floor_tool(config: MultiCameraConfig, path: Path, *, check: bool) -> int:
    if len(config.cameras) != 2:
        raise ValueError("The click calibration tool needs two cameras. For additional cameras, enter landmarks in YAML.")
    with ExitStack() as resources:
        feeds = {}
        for camera in config.cameras:
            feed = CalibrationFeed(open_calibration_capture(camera, path), camera.mirror)
            resources.callback(feed.close)
            feeds[camera.id] = feed
        return floor_window(config, path, feeds, check=check)


def floor_window(
    config: MultiCameraConfig, path: Path, feeds: dict[str, CalibrationFeed], *, check: bool,
) -> int:
    ids = list(feeds)
    tile_width, tile_height = 480, 360
    dimensions: dict[str, tuple[int, int]] = {}
    slots: dict[str, tuple[int, int, int, int]] = {}
    matrices: dict[str, np.ndarray] = {}
    ready: set[str] = set()
    points: list[tuple[str, Point]] = []
    window = "Shared plane calibration"

    def mouse(event: int, x: int, y: int, _flags: int, _data: object) -> None:
        if event == cv2.EVENT_RBUTTONDOWN and points and not check:
            points.pop()
        if event != cv2.EVENT_LBUTTONDOWN or check or not 0 <= x < 2 * tile_width:
            return
        camera = ids[x // tile_width]
        if camera not in ready or camera != ids[len(points) % 2]:
            return
        left, top, width, height = slots[camera]
        if left <= x < left + width and top <= y < top + height:
            points.append((camera, (round((x - left) / (width - 1), 5),
                                    round((y - top) / (height - 1), 5))))

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, mouse)
    try:
        while True:
            canvas = np.zeros((tile_height + 38, tile_width * 2, 3), np.uint8)
            now = monotonic()
            ready.clear()
            latest = {camera: feed.latest for camera, feed in feeds.items()}
            dimensions = {camera: (sample[1].shape[1], sample[1].shape[0])
                          for camera, sample in latest.items() if sample is not None}
            if check and len(dimensions) == len(feeds) and not matrices:
                matrices = calibrate_geometry(config, dimensions)
            for index, spec in enumerate(config.cameras):
                camera = spec.id
                feed = feeds[camera]
                sample = latest[camera]
                status = "Waiting for frames"
                if sample is not None:
                    stamp, frame = sample
                    height, width = frame.shape[:2]
                    scale = min(tile_width / width, tile_height / height)
                    width = max(2, round(width * scale))
                    height = max(2, round(height * scale))
                    left = index * tile_width + (tile_width - width) // 2
                    top = (tile_height - height) // 2
                    slots[camera] = (left, top, width, height)
                    tile = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                    if camera in matrices:
                        draw_floor_grid(tile, matrices[camera])
                    canvas[top:top + height, left:left + width] = tile
                    if feed.live and now - stamp > STALE_FRAME_SECONDS:
                        status = "Feed stalled"
                    elif feed.first_frame_at is not None and now - feed.first_frame_at < CAMERA_WARMUP_SECONDS:
                        status = "Adjusting exposure and focus"
                    else:
                        status = "Live" if feed.live else "Recorded frame"
                        ready.add(camera)
                label = f"{spec.name}  |  {status}"
                x = index * tile_width + 10
                cv2.rectangle(canvas, (x - 5, 5), (x + 340, 28), (20, 20, 20), -1)
                cv2.putText(canvas, label, (x, 22), 0, 0.48, (220, 235, 255), 1, cv2.LINE_AA)
            markers = points
            if check and matrices and config.calibration is not None:
                markers = []
                for landmark in config.calibration.landmarks:
                    for camera in ids:
                        x, y = landmark[camera]
                        if max(x, y) > 1:
                            w, h = dimensions[camera]
                            x, y = x / w, y / h
                        markers.append((camera, (x, y)))
            for index, (camera, (x, y)) in enumerate(markers):
                left, top, width, height = slots[camera]
                pt = (left + round(x * (width - 1)), top + round(y * (height - 1)))
                cv2.circle(canvas, pt, 5, (100, 255, 190), -1)
                cv2.putText(canvas, str(index // 2 + 1), (pt[0] + 7, pt[1]), 0, 0.5, (100, 255, 190), 1)
            instruction = "Q close" if check else f"Click landmark {len(points)//2 + 1} in {ids[len(points)%2]}   |   S save   Q quit"
            cv2.putText(canvas, instruction, (10, tile_height + 25), 0, 0.55, (220, 235, 255), 1)
            cv2.imshow(window, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key in (27, ord("q")) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) == 0:
                return 0
            if key == ord("s") and not check and len(points) >= 8 and len(points) % 2 == 0:
                landmarks = [{"name": f"landmark_{i//2 + 1}", ids[0]: points[i][1], ids[1]: points[i+1][1]}
                             for i in range(0, len(points), 2)]
                trial = config.model_copy(update={"calibration": (config.calibration or FloorCalibration()).model_copy(
                    update={"landmarks": landmarks})})
                try:
                    calibrate_geometry(trial, dimensions)
                    save_landmarks(path, landmarks)
                except ValueError as exc:
                    LOG.warning("%s", exc)
                    continue
                LOG.info("Saved %d landmarks to %s", len(landmarks), path)
                return 0
    finally:
        cv2.destroyWindow(window)
