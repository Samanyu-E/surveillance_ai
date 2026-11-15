import argparse
import importlib
import logging
import sys
from time import monotonic

LOG = logging.getLogger(__name__)


def doctor(camera: int, weights: str | None) -> bool:
    version_ok = (3, 11) <= sys.version_info[:2] < (3, 14)
    LOG.info("%s Python %s (supported: 3.11–3.13)", "PASS" if version_ok else "FAIL", sys.version.split()[0])
    dependencies_ok = True
    for module in ("cv2", "numpy", "torch", "ultralytics", "lap", "yaml", "pydantic"):
        try:
            importlib.import_module(module)
            LOG.info("PASS dependency %s", module)
        except (ImportError, OSError) as exc:
            LOG.error("FAIL dependency %s: %s", module, exc)
            dependencies_ok = False
    if not dependencies_ok:
        return False

    from .capture import Capture
    from .preview import inference_frame
    from .vision import Vision

    vision = None
    try:
        vision = Vision(weights=weights)
        LOG.info("PASS model %s, selected device %s", vision.weights, vision.device)
    except (RuntimeError, OSError, ValueError) as exc:
        LOG.error("FAIL model: %s", exc)

    capture = None
    try:
        capture = Capture(str(camera), native_resolution=True)
        frame = capture.first_frame()
        LOG.info("PASS camera %d returned a non-black frame", camera)
        if vision is None:
            return False
        vision.track(inference_frame(frame))
        started = monotonic()
        for _ in range(16):
            frame = capture.read()
            if frame is None:
                raise RuntimeError("Camera stopped delivering frames")
            vision.track(inference_frame(frame))
        fps = 16 / (monotonic() - started)
        LOG.info("%s %.1f camera + tracking FPS on %s (target 15)", "PASS" if fps >= 15 else "FAIL", fps, vision.device)
        return version_ok and fps >= 15
    except (RuntimeError, OSError) as exc:
        LOG.error("FAIL camera or tracking: %s", exc)
        return False
    finally:
        if capture is not None:
            capture.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check MacBook camera and tracking performance")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--weights")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return 0 if doctor(args.camera, args.weights) else 1


if __name__ == "__main__":
    raise SystemExit(main())
