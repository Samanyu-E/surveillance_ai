import logging
import sys
from time import monotonic
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from .capture import Frame
from .config import Config
from .tracks import Track

LOG = logging.getLogger(__name__)


class Vision:
    def __init__(
        self, config: Config | None = None, *, weights: str | None = None, image_size: int = 640,
    ) -> None:
        cached = Path(sys.prefix) / "weights" / "yolov8n.pt"
        default = str(cached) if cached.is_file() else "yolov8n.pt"
        self.weights = config.weights if config else weights or default
        self.image_size = config.image_size if config else image_size
        self.classes = [0, 24, 26, 28] if config else [0]
        self.model = YOLO(self.weights)
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.started = monotonic()
        self.frames = 0
        self.reset_tracks = False

    def track(self, frame: Frame) -> list[Track]:
        options = dict(
            persist=True, tracker="bytetrack.yaml", classes=self.classes,
            imgsz=self.image_size, verbose=False,
        )
        try:
            result = self.model.track(frame, device=self.device, **options)[0]
        except (RuntimeError, NotImplementedError):
            if self.device != "mps":
                raise
            LOG.warning("MPS inference failed. Restarting tracker on CPU.", exc_info=True)
            self.device = "cpu"
            self.model = YOLO(self.weights)
            self.reset_tracks = True
            result = self.model.track(frame, device=self.device, **options)[0]
        self.frames += 1
        boxes = result.boxes
        ids = boxes.id
        if ids is None:
            return []
        return [
            Track(int(track_id), result.names[int(kind)], (x1, y1, x2, y2))
            for track_id, kind, (x1, y1, x2, y2) in zip(
                ids.tolist(), boxes.cls.tolist(), boxes.xyxyn.tolist(), strict=True
            )
        ]

    @property
    def fps(self) -> float:
        return self.frames / max(monotonic() - self.started, 0.001)


def annotate(frame: Frame, tracks: list[Track], config: Config, status: str) -> Frame:
    height, width = frame.shape[:2]
    for track in tracks:
        left, top, right, bottom = track.box
        start = (int(left * width), int(top * height))
        cv2.rectangle(frame, start, (int(right * width), int(bottom * height)), (90, 230, 100), 2)
        cv2.putText(frame, f"{track.kind} #{track.id}", start, 0, 0.55, (90, 230, 100), 2)
    polygon = np.array([(x * width, y * height) for x, y in config.counter_zone], np.int32)
    cv2.polylines(frame, [polygon], True, (255, 180, 60), 2)
    a, b = [(int(x * width), int(y * height)) for x, y in config.exit_line]
    cv2.line(frame, a, b, (60, 140, 255), 2)
    cv2.putText(frame, status, (12, 26), 0, 0.65, (255, 255, 255), 2)
    return frame


class MultiVision(Vision):
    """Share detection weights while keeping ByteTrack state per source."""

    def __init__(self, camera_ids: list[str], *, weights: str | None, image_size: int) -> None:
        super().__init__(weights=weights, image_size=image_size)
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import YAML, IterableSimpleNamespace
        from ultralytics.utils.checks import check_yaml

        settings = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        self.trackers = {key: BYTETracker(settings) for key in camera_ids}

    def batch(self, frames: dict[str, Frame]) -> dict[str, list[Track]]:
        options = dict(conf=0.1, classes=[0], imgsz=self.image_size, verbose=False)
        images = list(frames.values())
        try:
            results = self.model.predict(images, device=self.device, **options)
        except (RuntimeError, NotImplementedError):
            if self.device != "mps":
                raise
            LOG.warning("MPS batch inference failed. Continuing on CPU.")
            self.device = "cpu"
            self.model = YOLO(self.weights)
            results = self.model.predict(images, device=self.device, **options)

        output = {}
        for camera, result in zip(frames, results, strict=True):
            height, width = result.orig_shape
            output[camera] = [
                Track(int(track_id), "person", (x1 / width, y1 / height, x2 / width, y2 / height))
                for x1, y1, x2, y2, track_id, *_ in self.trackers[camera].update(result.boxes.cpu().numpy())
            ]
        return output
