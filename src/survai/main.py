import argparse
import logging
from pathlib import Path

import yaml

from .config import CameraConfig, MultiCameraConfig, WalkoutConfig, load_walkout_config, save_config
from .detectors.walkout import VisitState, WalkoutDetector, WalkoutEvent
from .synthetic import demo_tracks

LOG = logging.getLogger(__name__)
DEMO_CONFIG = Path(__file__).with_name("walkout.demo.yaml")
LOCAL_CONFIG = Path("walkout.local.yaml")
MULTI_CONFIG = Path("walkout.multi.yaml")


def selftest(config: WalkoutConfig) -> bool:
    passed = True
    for name, paid, expected in (("Unpaid exit", False, 1), ("Paid exit", True, 0)):
        detector = WalkoutDetector(config)
        events: list[WalkoutEvent] = []
        states: set[VisitState] = set()
        for now, tracks in demo_tracks(paid):
            events.extend(detector.update(tracks, now))
            states.update(visit.state for visit in detector.visits.values())
        ok = (
            VisitState.DINING in states
            and (VisitState.PAID in states) == paid
            and len(events) == expected
            and all(event.exit_type == "track_lost" for event in events)
        )
        LOG.info(
            "%s %s: %d alert(s), expected %d",
            "PASS" if ok else "FAIL", name, len(events), expected,
        )
        passed &= ok
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="MacBook walk-out detection demo")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--selftest", action="store_true", help="Run synthetic demo takes")
    modes.add_argument("--selftest-multi", action="store_true", help="Test two synthetic camera feeds")
    modes.add_argument("--eval-reid", action="store_true", help="Measure matches, merges, and splits in a labeled synthetic run")
    modes.add_argument("--list-cameras", action="store_true", help="Find usable camera indices")
    modes.add_argument("--web", action="store_true", help="Configure and run the cameras from the browser")
    modes.add_argument("--demo-mode", action="store_true", help="Run the mirrored preview")
    modes.add_argument("--calibrate", action="store_true", help="Draw and save zones in the mirrored preview")
    modes.add_argument("--calibrate-floor", action="store_true", help="Click shared landmarks in two camera views")
    modes.add_argument("--check-calibration", action="store_true", help="Verify landmarks and view the projected grid")
    modes.add_argument("--doctor-multi", action="store_true", help="Check both inputs and shared calibration")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--camera", default=None, help="Camera index, or camera id for multi-camera calibration")
    sources.add_argument("--source", type=Path, help="Replay a recorded video")
    sources.add_argument("--sources", nargs="+", help="Two to four camera indexes or synchronized recordings")
    parser.add_argument("--grid", action="store_true", help="Show all cameras in the desktop preview")
    parser.add_argument("--floor-grid", action="store_true", help="Draw the calibrated plane over the camera feeds")
    parser.add_argument("--camera-a", type=int, help="Override the first live camera index")
    parser.add_argument("--camera-b", type=int, help="Override the second live camera index")
    parser.add_argument("--config", type=Path, help="Use a YAML config (default: walkout.local.yaml if present)")
    parser.add_argument("--weights", help="Use a local YOLO weights file")
    parser.add_argument("--record", type=Path, help="Save the raw camera feed to an MP4 file")
    parser.add_argument("--headless", action="store_true", help="Run without the preview window")
    parser.add_argument("--dashboard", action="store_true", help="Serve the local dashboard")
    parser.add_argument("--port", type=int, help="Require a specific dashboard port (default: prefer 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Keep --web from opening a browser tab")
    parser.add_argument("--output", type=Path, default=Path("alerts"), help="Alert evidence directory")
    parser.add_argument("--whatsapp", action="store_true", help="Enable Twilio delivery with configured credentials")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.selftest:
        return 0 if selftest(load_walkout_config(DEMO_CONFIG)) else 1
    if args.selftest_multi or args.eval_reid:
        from .multi_test import geometry_selftest, selftest_multi

        appearance_ok = selftest_multi(load_walkout_config(DEMO_CONFIG), evaluate=args.eval_reid)
        geometry_ok = geometry_selftest()
        return 0 if appearance_ok and geometry_ok else 1
    if args.list_cameras:
        from .preview import list_cameras

        return list_cameras()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.web:
        from .web import run_web

        try:
            defaults = load_walkout_config(LOCAL_CONFIG if LOCAL_CONFIG.exists() else DEMO_CONFIG)
            return run_web(args.config or MULTI_CONFIG, defaults, output=args.output, port=args.port,
                           weights=args.weights, whatsapp=args.whatsapp, open_browser=not args.no_browser)
        except (RuntimeError, OSError, ValueError) as exc:
            LOG.error("%s", exc)
            return 1
        except KeyboardInterrupt:
            return 0
    if args.calibrate and (args.headless or args.dashboard or args.record or args.whatsapp):
        parser.error("--calibrate uses a separate preview window")
    floor_mode = args.calibrate_floor or args.check_calibration or args.doctor_multi
    multi_mode = args.sources or floor_mode or args.camera_a is not None or args.camera_b is not None
    destination = args.config or (MULTI_CONFIG if multi_mode or (args.camera and not args.camera.isdecimal()) else LOCAL_CONFIG)
    use_default = (args.calibrate or args.config is None) and not destination.exists()
    config_path = DEMO_CONFIG if use_default else destination
    source = str(args.source.resolve() if args.source else args.camera or "0")
    try:
        data = yaml.safe_load(config_path.read_text())
        if floor_mode and (not isinstance(data, dict) or "cameras" not in data):
            parser.error("A multi-camera YAML configuration is required")
        if multi_mode or (isinstance(data, dict) and "cameras" in data):
            defaults = load_walkout_config(LOCAL_CONFIG if LOCAL_CONFIG.exists() else DEMO_CONFIG)
            if isinstance(data, dict) and "cameras" in data:
                multi = MultiCameraConfig.model_validate(data)
            else:
                defaults = load_walkout_config(config_path) if config_path != DEMO_CONFIG else defaults
                multi = MultiCameraConfig(cameras=[
                    CameraConfig(id=f"cam{i}", name=f"Camera {i}", source=value)
                    for i, value in enumerate(args.sources or ["0", "1"], 1)
                ])
            if args.sources:
                if len(args.sources) != len(multi.cameras):
                    if args.config is not None and isinstance(data, dict) and "cameras" in data:
                        parser.error("--sources needs one source per configured camera")
                    multi.cameras = [
                        CameraConfig(id=f"cam{i}", name=f"Camera {i}", source=value)
                        for i, value in enumerate(args.sources, 1)
                    ]
                for camera, value in zip(multi.cameras, args.sources, strict=True):
                    camera.source = value if value.isdecimal() else str(Path(value).resolve())
                multi = MultiCameraConfig.model_validate(multi.model_dump())
            for index, source_override in enumerate((args.camera_a, args.camera_b)):
                if source_override is not None:
                    multi.cameras[index].source = source_override
            multi = MultiCameraConfig.model_validate(multi.model_dump())
            if floor_mode:
                from .floor_calibration import floor_tool

                if args.calibrate_floor and args.headless:
                    parser.error("--calibrate-floor needs the click window")
                return floor_tool(multi, config_path, check=not args.calibrate_floor,
                                  headless=args.headless or args.doctor_multi, doctor=args.doctor_multi)
            defaults = multi.walkout or defaults
            if args.calibrate:
                from .calibrate import run_calibration

                camera = next((item for item in multi.cameras if item.id == args.camera), None)
                if camera is None:
                    parser.error("Choose a configured camera id with --camera, such as cam1")
                if not camera.walkout_enabled:
                    parser.error(f"{camera.name} is a security feed. Calibrate zones on a walk-out camera.")

                def save_layout(layout: WalkoutConfig) -> None:
                    camera.zones = layout.model_dump(include={"anchor", "dining_zone", "pay_zone", "exit_band", "exit_line"})
                    save_config(destination, multi)

                return run_calibration(camera.layout(defaults), resolve_source(camera.source, config_path),
                                       destination, mirror=camera.mirror, save=save_layout)
            if args.source or args.camera:
                parser.error("For multi-camera mode, select sources in YAML or with --sources")
            for camera in multi.cameras:
                camera.source = resolve_source(camera.source, config_path)
            from .multi_camera import run_multi

            return run_multi(multi, defaults, weights=args.weights, output=args.output,
                             dashboard_port=(args.port or 8765) if args.dashboard else None,
                             allow_fallback=args.port is None, headless=args.headless, grid=args.grid,
                             whatsapp=args.whatsapp, record=args.record, floor_grid=args.floor_grid)
        if args.camera is not None and not args.camera.isdecimal():
            parser.error("--camera must be a non-negative index in single-camera mode")
        config = load_walkout_config(config_path)
        if args.calibrate:
            from .calibrate import run_calibration

            return run_calibration(config, source, destination)
        from .preview import run_preview

        return run_preview(
            config, source, args.weights, args.record, args.headless,
            output=args.output, dashboard_port=(args.port or 8765) if args.dashboard else None,
            dashboard_port_fallback=args.port is None, whatsapp=args.whatsapp,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        LOG.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 0


def resolve_source(source: str | int, config_path: Path) -> str:
    value = str(source)
    return value if value.isdecimal() else str((config_path.parent / value).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
