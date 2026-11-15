import asyncio
import json
import re
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from dataclasses import asdict
from errno import EADDRINUSE
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from .llm import write_summary
from .detectors.walkout import VisitState
from .state import State


class Dashboard(AbstractContextManager[State]):
    def __init__(
        self, directory: Path, port: int, *, allow_fallback: bool = False,
        configure: Callable[[FastAPI, State], None] | None = None,
    ) -> None:
        self.state = State()
        self.socket = socket.socket()
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.socket.bind(("127.0.0.1", port))
            except OSError as exc:
                if exc.errno != EADDRINUSE:
                    raise
                if not allow_fallback:
                    raise RuntimeError(f"Dashboard port {port} is in use. Choose another with --port.") from exc
                self.socket.bind(("127.0.0.1", 0))
            self.socket.listen()
        except (OSError, RuntimeError):
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        app = create_app(self.state, directory, summary_model=None)
        if configure is not None:
            configure(app, self.state)
        self.server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning", timeout_graceful_shutdown=1))
        self.thread = Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, name="dashboard")

    def __enter__(self) -> State:
        self.thread.start()
        deadline = monotonic() + 5
        while not self.server.started and self.thread.is_alive() and monotonic() < deadline:
            sleep(0.02)
        if not self.server.started:
            self.__exit__(None, None, None)
            raise RuntimeError("The dashboard could not start")
        return self.state

    def __exit__(self, *_args: object) -> None:
        self.server.should_exit = True
        self.thread.join()
        self.socket.close()


def create_app(state: State, directory: Path, summary_model: str | None) -> FastAPI:
    app = FastAPI(title="Surveillance.ai")
    with state.lock:
        state.activity.open(directory)

    @app.get("/")
    def dashboard() -> FileResponse:
        return FileResponse(Path(__file__).with_name("dashboard.html"))

    @app.get("/api/status")
    def status() -> dict[str, object]:
        with state.lock:
            return {
                "status": state.status,
                "alert_active": state.alert_active,
                "total_alerts": state.counts.total(),
                "door_counts": state.door_counts,
                "door_activity": [
                    asdict(entry) | {"in_store": entry.crossing.in_store}
                    for entry in reversed(state.door_activity)
                ],
                "activity_log": [asdict(entry) for entry in state.activity.newest_first()],
                "walkout_enabled": state.walkout_enabled,
                "customers_in_view": sum(profile.visible and not profile.employee for profile in state.profiles.values()),
            }

    @app.get("/api/alerts")
    def history() -> list[dict[str, object]]:
        with state.lock:
            return [asdict(event) for _, event in reversed(state.events)]

    @app.get("/api/visits")
    def active_visits() -> list[dict[str, object]]:
        with state.lock:
            return [
                {
                    "track_id": profile.track_id,
                    "first_seen": profile.first_seen,
                    "last_seen": profile.last_seen,
                    "state": profile.state,
                    "visible": profile.visible,
                    "employee": profile.employee,
                    "cameras": profile.cameras,
                    "dwell_seconds": profile.dwell_seconds,
                    "match_confidence": profile.match_confidence,
                }
                for profile in state.profiles.values()
            ]

    @app.post("/api/visits/{track_id}/pay", status_code=202)
    def mark_guest_paid(track_id: int) -> None:
        with state.lock:
            if state.status != "Monitoring" or not state.walkout_enabled:
                raise HTTPException(409, "Start walk-out monitoring before confirming payment.")
            profile = state.profiles.get(track_id)
            if profile is None or profile.state == VisitState.EXITED:
                raise HTTPException(404, "This guest is no longer being tracked.")
            if profile.employee or profile.state == VisitState.EMPLOYEE:
                raise HTTPException(409, "Employees do not have a payment status.")
            if not profile.visible:
                raise HTTPException(409, "This guest is no longer in view.")
            if profile.state != VisitState.PAID:
                state.paid_requests.add(track_id)

    @app.delete("/api/alerts/{alert_id}", status_code=204)
    def delete_alert(alert_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", alert_id):
            raise HTTPException(404)
        state.delete_alert(alert_id)

    @app.get("/api/summary")
    def summary() -> dict[str, object]:
        metrics = state.metrics()
        busiest = metrics["busiest_period_start_seconds"]
        period = f"{busiest}s into this session" if busiest is not None else "unavailable"
        fallback = (
            f"This session recorded {metrics['total_alerts']} alerts. "
            f"The peak queue held {metrics['peak_queue']} people. "
            f"The busiest minute starts at {period}." if busiest is not None
            else "The session has no camera observations yet."
        )
        return {**metrics, "summary": write_summary(summary_model, metrics, fallback) if summary_model else fallback}

    @app.get("/snapshots/{name}")
    def snapshot(name: str) -> FileResponse:
        path = directory / name
        if not re.fullmatch(r"[0-9a-f]{32}(?:-face)?\.jpg", name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/clips/{name}")
    def clip(name: str) -> FileResponse:
        path = directory / name
        if not re.fullmatch(r"[0-9a-f]{32}\.mp4", name) or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="video/mp4")

    @app.get("/video")
    async def video(request: Request) -> StreamingResponse:
        return video_stream(request)

    @app.get("/api/cameras")
    def cameras() -> list[dict[str, object]]:
        with state.lock:
            return [{"id": key, "name": feed.name, "status": feed.status, "fps": feed.fps}
                    for key, feed in state.cameras.items()]

    @app.get("/api/floor")
    def floor() -> dict[str, object] | None:
        with state.lock:
            return state.floor

    @app.get("/video/{camera_id}")
    async def camera_video(request: Request, camera_id: str) -> StreamingResponse:
        if camera_id == "grid":
            return video_stream(request)
        with state.lock:
            if camera_id not in state.cameras:
                raise HTTPException(404)
        return video_stream(request, camera_id)

    def video_stream(request: Request, camera_id: str | None = None) -> StreamingResponse:
        async def frames() -> AsyncIterator[bytes]:
            seen = None
            while not await request.is_disconnected():
                with state.lock:
                    if camera_id is not None and camera_id not in state.cameras:
                        return
                    frame = state.frame if camera_id is None else state.cameras[camera_id].jpeg
                if frame and frame is not seen:
                    seen = frame
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.025)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        async def messages() -> AsyncIterator[str]:
            cursor = request.headers.get("last-event-id", "")
            seen = int(cursor) if cursor.isdecimal() and len(cursor) < 20 else 0
            idle = 0
            initial = True
            yield ": connected\n\n"
            while not await request.is_disconnected():
                with state.lock:
                    pending = [(index, event) for index, event in state.changes if index > seen]
                for index, event in pending:
                    seen = index
                    if isinstance(event, str):
                        yield f"id: {index}\nevent: alert-deleted\ndata: {event}\n\n"
                    else:
                        yield f"id: {index}\ndata: {json.dumps(asdict(event))}\n\n"
                if initial:
                    # Clients may enable sound only after the saved history is replayed.
                    yield "event: ready\ndata: {}\n\n"
                    initial = False
                idle = 0 if pending else idle + 1
                if idle == 150:
                    yield ": keep-alive\n\n"
                    idle = 0
                await asyncio.sleep(0.1)

        return StreamingResponse(messages(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
