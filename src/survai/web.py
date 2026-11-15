import logging
import webbrowser
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal
from threading import Event, Lock, Thread
from time import monotonic, sleep

import cv2
import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .alerts.whatsapp import WhatsAppDeliveryError, WhatsAppSettings, load_settings, save_settings, send_message
from .capture import Capture
from .config import CameraConfig, FloorCalibration, MultiCameraConfig, WalkoutConfig, save_config
from .floor_calibration import (
    CAMERA_WARMUP_SECONDS,
    STALE_FRAME_SECONDS,
    CalibrationFeed,
    calibrate_geometry,
    open_calibration_capture,
)
from .server import Dashboard
from .state import State

LOG = logging.getLogger(__name__)
ZONE_FIELDS = {"anchor", "dining_zone", "pay_zone", "exit_band", "exit_line"}


class Cameras(BaseModel):
    cameras: list[CameraConfig]


class Settings(Cameras):
    calibration: FloorCalibration | None
    walkout: WalkoutConfig
    plane: Literal["floor", "head"]


class StartOptions(BaseModel):
    record: bool = False
    floor_grid: bool = False


class WebSession:
    def __init__(
        self, path: Path, config: MultiCameraConfig, defaults: WalkoutConfig, state: State,
        output: Path, weights: str | None, whatsapp: bool,
    ) -> None:
        self.path = path
        self.config = config
        self.defaults = defaults
        self.state = state
        self.output = output
        self.weights = weights
        self.whatsapp = whatsapp
        self.lock = Lock()
        self.stop = Event()
        self.pay = Event()
        self.worker: Thread | None = None
        self.mode = "idle"
        self.error = ""
        self.previews: dict[str, CalibrationFeed] = {}
        self.preview_status: dict[str, str] = {}
        self.preview_specs: dict[str, CameraConfig] = {}
        self.camera_indexes: list[int] | None = None
        self.state.status = "Monitoring stopped"
        self.state.walkout_enabled = any(camera.walkout_enabled for camera in config.active_cameras)

    def settings(self) -> dict[str, object]:
        with self.lock:
            defaults = self.config.walkout or self.defaults
            cameras = []
            for camera in self.config.cameras:
                data = camera.model_dump(mode="json")
                if camera.walkout_enabled:
                    overrides = {key: value for key, value in camera.zones.items() if key not in {"dining", "pay"}}
                    layout = camera.layout(defaults).model_dump(mode="json", include=ZONE_FIELDS)
                    data["zones"] = overrides | layout
                cameras.append(data)
            return {"cameras": cameras, "calibration": self.config.calibration, "walkout": defaults,
                    "plane": self.config.fusion.plane}

    def status(self) -> dict[str, object]:
        with self.lock:
            statuses = self.preview_status.copy()
            now = monotonic()
            for key, feed in self.previews.items():
                latest = feed.latest
                if latest is None:
                    continue
                if feed.live and now - latest[0] > STALE_FRAME_SECONDS:
                    statuses[key] = "Waiting for camera frames"
                elif feed.first_frame_at is not None and now - feed.first_frame_at < CAMERA_WARMUP_SECONDS:
                    statuses[key] = "Adjusting exposure and focus"
                else:
                    statuses[key] = "Ready"
            return {"mode": self.mode, "error": self.error, "previews": statuses,
                    "camera_indexes": self.camera_indexes}

    def save(self, settings: Settings) -> None:
        with self.lock:
            if self.mode not in {"idle", "preview"}:
                raise HTTPException(409, "Stop monitoring before changing setup.")
            current = self.config
            walkout = current.walkout if settings.walkout == self.defaults and current.walkout is None else settings.walkout
            updated = MultiCameraConfig.model_validate(
                current.model_dump() | settings.model_dump(exclude={"plane"}) | {
                    "walkout": walkout, "fusion": current.fusion.model_dump() | {"plane": settings.plane},
                }
            )
            self.sources(updated.active_cameras)
            old_geometry = {item.id: (str(item.source), item.mirror) for item in current.cameras}
            new_geometry = {item.id: (str(item.source), item.mirror) for item in updated.cameras}
            if updated.calibration is not None and updated.calibration == current.calibration and old_geometry != new_geometry:
                raise ValueError("Clear or redraw shared landmarks after changing a camera source or its mirroring.")
            for camera in updated.cameras:
                if camera.walkout_enabled:
                    camera.layout(settings.walkout)
            if updated.calibration is not None and updated.calibration != current.calibration:
                dimensions = {}
                for camera in updated.cameras:
                    feed = self.previews.get(camera.id)
                    latest = feed.latest if feed is not None else None
                    spec = self.preview_specs.get(camera.id)
                    if latest is not None and spec is not None and (camera.source, camera.mirror) == (spec.source, spec.mirror):
                        image = latest[1]
                        dimensions[camera.id] = (image.shape[1], image.shape[0])
                    else:
                        # Browser clicks are normalized. Legacy pixel coordinates need their source frame dimensions.
                        dimensions[camera.id] = (1, 1)
                calibrate_geometry(updated, dimensions)
            if updated != current or not self.path.exists():
                save_config(self.path, updated)
                self.config = updated
            with self.state.lock:
                self.state.walkout_enabled = any(camera.walkout_enabled for camera in updated.active_cameras)

    def sources(self, cameras: list[CameraConfig]) -> list[CameraConfig]:
        resolved = []
        for camera in cameras:
            value = str(camera.source)
            if not value.isdecimal():
                path = (self.path.parent / value).resolve()
                if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"} or not path.is_file():
                    raise ValueError(f"{camera.name}: choose a camera index or an existing local video file.")
                value = str(path)
            resolved.append(camera.model_copy(update={"source": value}))
        if len({str(camera.source).isdecimal() for camera in resolved}) > 1:
            raise ValueError("Choose live cameras for all inputs or a recording for every input.")
        return resolved

    def scan(self) -> None:
        def probe() -> None:
            found = []
            for index in range(6):
                if self.stop.is_set():
                    return
                capture = None
                try:
                    capture = Capture(str(index), native_resolution=True)
                    capture.first_frame(timeout=1.5)
                    found.append(index)
                except (OSError, RuntimeError, cv2.error):
                    continue
                finally:
                    if capture is not None:
                        capture.close()
            with self.lock:
                self.camera_indexes = found

        self.launch("scanning", probe)

    def preview(self, choices: Cameras) -> None:
        cameras = MultiCameraConfig(cameras=choices.cameras).active_cameras
        self.sources(cameras)
        self.launch("preview", lambda: self.run_preview(cameras))

    def run_preview(self, cameras: list[CameraConfig]) -> None:
        with ExitStack() as resources:
            for camera in cameras:
                if self.stop.is_set():
                    break
                try:
                    feed = CalibrationFeed(open_calibration_capture(camera, self.path), camera.mirror)
                except (ValueError, OSError, RuntimeError, cv2.error) as exc:
                    with self.lock:
                        hint = " Stop cameras, then use Find cameras to check the current index." if (
                            str(camera.source).isdecimal()
                        ) else ""
                        self.preview_status[camera.id] = f"{exc}{hint}"
                    continue
                resources.callback(feed.close, wait=True)
                with self.lock:
                    self.previews[camera.id] = feed
                    self.preview_specs[camera.id] = camera
            self.stop.wait()

    def jpeg(self, camera_id: str) -> bytes:
        with self.lock:
            feed = self.previews.get(camera_id)
        if feed is None:
            raise HTTPException(404, "Waiting for frames.")
        latest = feed.latest
        if latest is None or (feed.live and monotonic() - latest[0] > STALE_FRAME_SECONDS):
            raise HTTPException(404, "Waiting for frames.")
        frame = latest[1]
        if frame.shape[1] > 960:
            frame = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise HTTPException(503, "Could not encode preview frame.")
        return encoded.tobytes()

    def start(self, options: StartOptions) -> None:
        with self.lock:
            config = self.config.model_copy(deep=True)
            if len(config.active_cameras) != len(config.cameras):
                config.calibration = None
            config.cameras = self.sources(config.active_cameras)
            defaults = config.walkout or self.defaults
        if options.record and not str(config.cameras[0].source).isdecimal():
            raise ValueError("Recording requires live camera inputs.")
        record = None
        if options.record:
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
            record = self.path.parent / "recordings" / f"session-{stamp}.mp4"

        def monitor() -> None:
            from .multi_camera import run_multi

            run_multi(config, defaults, weights=self.weights, output=self.output, dashboard_port=None,
                      allow_fallback=False, headless=True, grid=False, whatsapp=self.whatsapp,
                      record=record, floor_grid=options.floor_grid, state=self.state,
                      stop=self.stop, mark_paid=self.pay, whatsapp_path=self.path.parent / ".env")

        self.launch("monitoring", monitor)

    def launch(self, mode: str, task: Callable[[], None]) -> None:
        def run() -> None:
            try:
                task()
            except Exception as exc:
                LOG.exception("Browser session failed")
                with self.lock:
                    if not self.stop.is_set():
                        self.error = str(exc)
            finally:
                with self.lock:
                    self.previews.clear()
                    self.preview_specs.clear()
                    self.preview_status.clear()
                    self.mode = "idle"
                    with self.state.lock:
                        self.state.status = "Monitoring stopped"
                        self.state.frame = None
                        self.state.cameras.clear()
                        self.state.profiles.clear()
                        self.state.paid_requests.clear()
                        self.state.floor = None
                        self.state.alert_active = False

        with self.lock:
            if self.mode != "idle":
                raise HTTPException(409, "Stop the current camera session first.")
            self.mode = mode
            self.error = ""
            self.stop.clear()
            self.pay.clear()
            if mode == "scanning":
                self.camera_indexes = None
            if mode == "monitoring":
                with self.state.lock:
                    self.state.status = "Starting cameras and model"
                    self.state.paid_requests.clear()
                    self.state.door_counts = None
                    self.state.door_activity.clear()
            self.worker = Thread(target=run, name=f"web-{mode}", daemon=True)
            self.worker.start()

    def request_stop(self) -> None:
        with self.lock:
            if self.mode != "idle":
                self.mode = "stopping"
                self.stop.set()
                with self.state.lock:
                    self.state.status = "Releasing cameras"

    def mark_paid(self) -> None:
        with self.lock:
            with self.state.lock:
                monitoring = self.state.status == "Monitoring"
            if self.mode != "monitoring" or not monitoring:
                raise HTTPException(409, "Start live monitoring before marking guests paid.")
            self.pay.set()

    def close(self) -> None:
        self.request_stop()
        if self.worker is not None:
            self.worker.join(timeout=5)


def install_web_routes(app: FastAPI, session: WebSession) -> None:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def protect_local_controls(request: Request, call_next):
        host = request.headers.get("host")
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" and request.headers.get("sec-fetch-mode") != "navigate":
            return Response(status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != f"http://{host}":
            return Response(status_code=403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_settings(_request: Request, exc: ValueError):
        detail = str(exc)
        if isinstance(exc, ValidationError):
            detail = " ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        return JSONResponse({"detail": detail}, status_code=422)

    @app.get("/setup")
    def setup() -> FileResponse:
        return FileResponse(Path(__file__).with_name("setup.html"), headers={"Cache-Control": "no-store"})

    @app.get("/employee-marker.png")
    def employee_marker() -> FileResponse:
        return FileResponse(Path(__file__).with_name("employee-marker.png"), media_type="image/png")

    @app.get("/setup.js")
    def setup_script() -> FileResponse:
        return FileResponse(Path(__file__).with_name("setup.js"), media_type="text/javascript",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/setup")
    def settings() -> dict[str, object]:
        return session.settings()

    @app.get("/api/whatsapp")
    def whatsapp_settings(response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return load_settings(session.path.parent / ".env", session.whatsapp).public()

    @app.put("/api/whatsapp")
    def update_whatsapp(settings: WhatsAppSettings, response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return save_settings(session.path.parent / ".env", settings, session.whatsapp).public()

    @app.post("/api/whatsapp/test")
    def test_whatsapp(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "no-store"
        settings = load_settings(session.path.parent / ".env", session.whatsapp)
        try:
            return send_message(settings, "Surveillance.ai test notification. WhatsApp alerts are connected.")
        except WhatsAppDeliveryError as exc:
            raise HTTPException(502 if settings.configured else 409, str(exc)) from None

    @app.put("/api/setup", status_code=204)
    def save(data: Settings) -> None:
        session.save(data)

    @app.get("/api/session")
    def status() -> dict[str, object]:
        return session.status()

    @app.post("/api/session/scan", status_code=202)
    def scan() -> None:
        session.scan()

    @app.post("/api/session/preview", status_code=202)
    def preview(data: Cameras) -> None:
        session.preview(data)

    @app.get("/api/session/preview/{camera_id}")
    def frame(camera_id: str) -> Response:
        return Response(session.jpeg(camera_id), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/session/start", status_code=202)
    def start(options: StartOptions) -> None:
        session.start(options)

    @app.post("/api/session/stop", status_code=202)
    def stop() -> None:
        session.request_stop()

    @app.post("/api/session/pay", status_code=202)
    def pay() -> None:
        session.mark_paid()


def run_web(
    path: Path, defaults: WalkoutConfig, *, output: Path, port: int | None, weights: str | None,
    whatsapp: bool, open_browser: bool,
) -> int:
    config = MultiCameraConfig.model_validate(yaml.safe_load(path.read_text())) if path.exists() else MultiCameraConfig(
        cameras=[
            CameraConfig(id="cam1", name="MacBook", source=0),
            CameraConfig(id="cam2", name="iPhone", source=1, walkout_enabled=False),
        ],
    )
    session: WebSession | None = None

    def configure(app: FastAPI, state: State) -> None:
        nonlocal session
        session = WebSession(path, config, defaults, state, output, weights, whatsapp)
        install_web_routes(app, session)

    dashboard = Dashboard(output, port or 8765, allow_fallback=port is None, configure=configure)
    assert session is not None
    with dashboard:
        try:
            url = f"http://127.0.0.1:{dashboard.port}/"
            LOG.info("Open %s to configure or start the camera feeds.", url)
            if open_browser:
                webbrowser.open(url)
            while dashboard.thread.is_alive():
                sleep(0.25)
        finally:
            session.close()
    return 0
