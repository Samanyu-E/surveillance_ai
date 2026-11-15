"""Best-effort face thumbnails from a tracked person's existing evidence crops."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceThumbnail:
    jpeg: bytes
    score: float


class FaceExtractor:
    def __init__(self) -> None:
        self.detector: cv2.CascadeClassifier | None = None
        self.loaded = False

    def best(self, images: Iterable[bytes]) -> FaceThumbnail | None:
        try:
            if not self.loaded:
                self.loaded = True
                model = Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml"
                self.detector = cv2.CascadeClassifier(str(model))
            if self.detector is None or self.detector.empty():
                return None
            best = None
            for jpeg in images:
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or min(frame.shape[:2]) < 32:
                    continue
                thumbnail = self._extract(frame)
                if thumbnail is not None and (best is None or thumbnail.score > best.score):
                    best = thumbnail
            return best
        except (OSError, AttributeError, cv2.error):
            LOG.debug("Face thumbnail unavailable; keeping the person snapshot", exc_info=True)
            return None

    def _extract(self, frame: np.ndarray) -> FaceThumbnail | None:
        height, width = frame.shape[:2]
        scale = min(1, 320 / max(height, width))
        small = cv2.resize(frame, (round(width * scale), round(height * scale)))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        assert self.detector is not None
        faces = self.detector.detectMultiScale(
            cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=6, minSize=(24, 24),
        )
        sh, sw = gray.shape
        # A head should be near the top of this person's crop. Ambiguous crops
        # with multiple plausible heads are skipped rather than picking a bystander.
        heads = [
            (int(x), int(y), int(w), int(h)) for x, y, w, h in faces
            if x >= 0 and y >= 0 and x + w <= sw and y + h <= sh
            and 0.15 * sw <= x + w / 2 <= 0.85 * sw and y + h / 2 <= 0.55 * sh
        ]
        if len(heads) != 1:
            return None
        x, y, w, h = heads[0]
        face = gray[y:y + h, x:x + w]
        sharpness = float(cv2.Laplacian(face, cv2.CV_32F).var())
        if sharpness < 25 or not 30 <= float(face.mean()) <= 235:
            return None
        left, top = max(0, round((x - w * 0.3) / scale)), max(0, round((y - h * 0.3) / scale))
        right = min(width, round((x + w * 1.3) / scale))
        bottom = min(height, round((y + h * 1.3) / scale))
        ok, encoded = cv2.imencode(".jpg", frame[top:bottom, left:right], [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None
        return FaceThumbnail(encoded.tobytes(), float(np.log1p(sharpness) + 0.5 * np.log1p(w * h)))
