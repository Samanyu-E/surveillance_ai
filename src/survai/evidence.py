import logging
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic, time

import cv2
import numpy as np

from .alerts import JsonlSink
from .capture import Frame
from .detectors.walkout import VisitEvent
from .events import Event
from .faces import FaceExtractor
from .identity import overlap
from .state import State
from .tracks import Track

LOG = logging.getLogger(__name__)
PRE_ROLL = 8
POST_ROLL = 2
CLIP_FPS = 12
SNAPSHOT_LOOKBACK = 3
RETENTION_INTERVAL = 3600
EVIDENCE_FILENAME = re.compile(r"[0-9a-f]{32}(?:-face\.jpg|\.(?:jpg|mp4))")


def expire_evidence(directory: Path, days: int, *, now: float) -> set[str]:
    cutoff = now - days * 86400
    deleted = set()
    for path in directory.iterdir():
        match = EVIDENCE_FILENAME.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                route = "snapshots" if path.suffix == ".jpg" else "clips"
                deleted.add(f"/{route}/{path.name}")
        except FileNotFoundError:
            continue
        except OSError:
            LOG.warning("Could not remove expired evidence %s", path, exc_info=True)
    return deleted


@dataclass(frozen=True)
class Sample:
    seconds: float
    jpeg: bytes


@dataclass(frozen=True)
class SnapshotCandidate:
    seconds: float
    score: float
    jpeg: bytes
    face_eligible: bool = True


def snapshot_candidates(
    frame: Frame, tracks: list[Track], seconds: float,
) -> dict[int, SnapshotCandidate]:
    height, width = frame.shape[:2]
    candidates = {}
    for track in tracks:
        left, top, right, bottom = track.box
        crop = frame[max(0, round(top * height)):round(bottom * height),
                     max(0, round(left * width)):round(right * width)]
        if min(crop.shape[:2]) < 8:
            continue
        gray = cv2.cvtColor(cv2.resize(crop, (80, 80), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        sharpness = cv2.Laplacian(gray, cv2.CV_32F).var()
        clipped = left <= 0.01 or right >= 0.99 or top <= 0.01 or bottom >= 0.99
        obstruction = max((overlap(track.box, other.box) for other in tracks if track.id != other.id), default=0)
        score = float(np.log1p(sharpness)) - 2 * clipped - 3 * obstruction
        scale = min(1, 480 / max(crop.shape[:2]))
        if scale < 1:
            crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)),
                                     max(1, round(crop.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", crop)
        if ok:
            candidates[track.id] = SnapshotCandidate(seconds, score, encoded.tobytes(), obstruction < 0.25)
    return candidates


@dataclass
class PendingClip:
    event: Event
    samples: list[Sample]

    @property
    def end(self) -> float:
        return self.event.source_seconds + POST_ROLL


class Evidence:
    def __init__(self, directory: Path, state: State, retention_days: int = 2) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state = state
        self.retention_days = retention_days
        self.next_cleanup = monotonic() + RETENTION_INTERVAL
        self._cleanup()
        self.samples: deque[Sample] = deque(maxlen=PRE_ROLL * CLIP_FPS + 10)
        self.snapshots: deque[dict[int, SnapshotCandidate]] = deque(maxlen=self.samples.maxlen)
        self.pending: list[PendingClip] = []
        self.next_sample = 0.0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="evidence")
        self.log = JsonlSink(directory, "alerts.jsonl")
        self.faces = FaceExtractor()

    def wants_frame(self, seconds: float) -> bool:
        return seconds + 1e-6 >= self.next_sample

    def observe(
        self, frame: Frame, seconds: float, *, snapshots: dict[int, SnapshotCandidate] | None = None,
    ) -> None:
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return
        sample = Sample(seconds, jpg.tobytes())
        self.samples.append(sample)
        self.snapshots.append(snapshots or {})
        self.next_sample = (int((seconds + 1e-6) * CLIP_FPS) + 1) / CLIP_FPS
        for pending in self.pending:
            if seconds <= pending.end + 1e-6:
                pending.samples.append(sample)

    def register(self, walkout: VisitEvent) -> Event:
        now = walkout.source_seconds
        event = Event(
            walkout.event_type, "warning", (walkout.track_id,), walkout.timestamp.isoformat(),
            walkout.message, now, dwell_seconds=walkout.dwell_seconds, exit_type=walkout.exit_type,
        )
        if event.type == "counter_wait":
            try:
                self.log.send(event)
            except OSError:
                LOG.exception("Could not write alerts.jsonl")
            return event
        event.clip_status = "pending"
        samples = [sample for sample in self.samples if sample.seconds >= now - PRE_ROLL]
        candidates = [
            batch[walkout.track_id] for batch in self.snapshots if walkout.track_id in batch
            and now - PRE_ROLL <= batch[walkout.track_id].seconds <= walkout.last_seen_seconds
        ]
        earlier = [candidate for candidate in candidates if candidate.seconds < walkout.last_seen_seconds]
        recent = [
            candidate for candidate in earlier
            if candidate.seconds >= walkout.last_seen_seconds - SNAPSHOT_LOOKBACK
        ]
        snapshot = max(recent or earlier or candidates, key=lambda candidate: (candidate.score, candidate.seconds), default=None)
        if snapshot is not None:
            name = f"{event.id}.jpg"
            try:
                (self.directory / name).write_bytes(snapshot.jpeg)
            except OSError:
                LOG.exception("Could not save alert snapshot")
            else:
                event.snapshot_path = f"/snapshots/{name}"
        # Keep the body snapshot and clip. Search at most one crop per half
        # second across pre-roll, including earlier frames where the guest faced us.
        face_frames: dict[int, SnapshotCandidate] = {}
        for candidate in candidates:
            if not candidate.face_eligible:
                continue
            bucket = int(candidate.seconds * 2)
            if bucket not in face_frames or candidate.score > face_frames[bucket].score:
                face_frames[bucket] = candidate
        face = self.faces.best(candidate.jpeg for candidate in sorted(
            face_frames.values(), key=lambda item: item.score, reverse=True,
        )[:16]) if face_frames else None
        if face is not None:
            name = f"{event.id}-face.jpg"
            try:
                (self.directory / name).write_bytes(face.jpeg)
            except OSError:
                LOG.exception("Could not save face thumbnail")
            else:
                event.face_path = f"/snapshots/{name}"
        event.clip_path = f"/clips/{event.id}.mp4"
        self.pending.append(PendingClip(event, samples))
        return event

    def advance(self, now: float) -> None:
        self.check_retention()
        remaining = []
        for pending in self.pending:
            if pending.end <= now + 1e-6:
                self.worker.submit(self._finish, pending, pending.end)
            else:
                remaining.append(pending)
        self.pending = remaining

    def check_retention(self) -> None:
        clock = monotonic()
        if clock >= self.next_cleanup:
            self.next_cleanup = clock + RETENTION_INTERVAL
            self.worker.submit(self._cleanup)

    def _cleanup(self) -> None:
        try:
            deleted = expire_evidence(self.directory, self.retention_days, now=time())
        except OSError:
            LOG.warning("Could not scan evidence directory %s", self.directory, exc_info=True)
            return
        if deleted:
            self.state.expire_evidence(deleted)
            LOG.info("Removed %d expired evidence files", len(deleted))

    def finish_pending(self, last_seconds: float) -> None:
        for pending in self.pending:
            self.worker.submit(self._finish, pending, last_seconds)
        self.pending.clear()

    def close(self, last_seconds: float) -> None:
        self.finish_pending(last_seconds)
        self.worker.shutdown()

    def _finish(self, pending: PendingClip, end: float) -> None:
        event = pending.event
        try:
            self._write_clip(pending, end)
        except (OSError, ValueError, RuntimeError, cv2.error):
            LOG.exception("Could not save alert clip")
            event = replace(event, clip_status="unavailable", clip_path=None)
        else:
            event = replace(event, clip_status="ready")
        self.state.finish_clip(event)
        try:
            self.log.send(event)
        except OSError:
            LOG.exception("Could not write alerts.jsonl")

    def _write_clip(self, pending: PendingClip, end: float) -> None:
        if not pending.samples:
            raise ValueError("No frames were captured for this clip")
        source = iter(pending.samples)
        current = next(source)
        following = next(source, None)
        first_time = current.seconds
        frame = self._decode(current)
        height, width = frame.shape[:2]
        width -= width % 2
        height -= height % 2
        temporary = self.directory / f".{pending.event.id}.tmp.mp4"
        destination = self.directory / f"{pending.event.id}.mp4"
        writer = cv2.VideoWriter(temporary.as_posix(), cv2.VideoWriter_fourcc(*"avc1"), CLIP_FPS, (width, height))
        try:
            try:
                if not writer.isOpened():
                    raise RuntimeError("The OpenCV H.264 encoder is unavailable")
                for index in range(max(1, round((end - first_time) * CLIP_FPS))):
                    seconds = first_time + index / CLIP_FPS
                    last = current
                    while following is not None and following.seconds <= seconds + 1e-6:
                        current = following
                        following = next(source, None)
                    if current is not last:
                        frame = self._decode(current)
                    writer.write(frame[:height, :width])
            finally:
                writer.release()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _decode(sample: Sample) -> Frame:
        frame = cv2.imdecode(np.frombuffer(sample.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode an evidence frame")
        return frame
