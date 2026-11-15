import sys
from time import monotonic, sleep

import cv2
from numpy.typing import NDArray
from numpy import uint8

Frame = NDArray[uint8]


class Capture:
    def __init__(self, source: str, *, native_resolution: bool = False) -> None:
        self.live = source.isdecimal()
        if self.live:
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            self.device = cv2.VideoCapture(int(source), backend)
            if not native_resolution:
                self.device.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.device.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        else:
            self.device = cv2.VideoCapture(source)
        if not self.device.isOpened():
            self.close()
            if self.live:
                raise RuntimeError(
                    f"Could not open camera index {source}. Check that the camera is connected and available."
                )
            raise RuntimeError(f"Could not open video {source!r}.")
        fps = self.device.get(cv2.CAP_PROP_FPS)
        self.fps = fps if 0 < fps < 1000 else 30.0

    def read(self) -> Frame | None:
        ok, frame = self.device.read()
        return frame if ok else None

    def first_frame(self, timeout: float = 3) -> Frame:
        deadline = monotonic() + timeout
        while True:
            frame = self.read()
            if frame is not None and frame.size and (not self.live or frame.max() > 8):
                return frame
            if not self.live or monotonic() >= deadline:
                break
            sleep(0.03)
        hint = ""
        if self.live and sys.platform == "darwin":
            hint = " Grant your terminal or IDE access in System Settings > Privacy & Security > Camera and restart it."
        raise RuntimeError(f"Source did not return a usable frame.{hint}")

    def close(self) -> None:
        self.device.release()
