from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import cv2
import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from survai.config import MultiCameraConfig, load_walkout_config
from survai.main import DEMO_CONFIG
from survai.server import create_app
from survai.state import State
from survai.web import WebSession, install_web_routes


@pytest.mark.parametrize(("walkout", "marker"), [(False, False), (False, True), (True, False), (True, True)])
def test_door_line_preserved_but_inactive_with_combined_walkout_and_marker(
    tmp_path: Path, walkout: bool, marker: bool,
) -> None:
    line = [[0.5, 0.2], [0.5, 0.8]]
    config = MultiCameraConfig(cameras=[{
        "id": "cam1", "name": "Camera", "source": 0, "walkout_enabled": False, "door_line": line,
    }])
    path = tmp_path / "setup.yaml"
    state = State()
    session = WebSession(path, config, load_walkout_config(DEMO_CONFIG), state, tmp_path, None, False)
    app = create_app(state, tmp_path, None)
    install_web_routes(app, session)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        settings = client.get("/api/setup").json()
        settings["cameras"][0].update(walkout_enabled=walkout, employee_marker=marker)
        assert client.put("/api/setup", json=settings).status_code == 204
        assert client.get("/api/setup").json()["cameras"][0]["door_line"] == line
        saved = yaml.safe_load(path.read_text())
        assert saved["cameras"][0]["door_line"] == line
        reloaded = MultiCameraConfig.model_validate(saved)
        assert reloaded.cameras[0].model_dump(mode="json")["door_line"] == line
        expected_active = None if walkout and marker else tuple(tuple(point) for point in line)
        assert reloaded.cameras[0].visible_door_line == expected_active

        settings["cameras"][0].update(walkout_enabled=True, employee_marker=True)
        assert client.put("/api/setup", json=settings).status_code == 204
        assert session.config.cameras[0].visible_door_line is None
        settings = client.get("/api/setup").json()
        settings["cameras"][0]["walkout_enabled"] = False
        assert client.put("/api/setup", json=settings).status_code == 204
        assert session.config.cameras[0].visible_door_line == tuple(tuple(point) for point in line)
        assert client.get("/api/setup").json()["cameras"][0]["door_line"] == line


def test_browser_previews_and_starts_with_saved_camera_layout(tmp_path: Path) -> None:
    config = MultiCameraConfig(cameras=[
        {"id": "mac", "name": "MacBook", "source": 0},
        {"id": "phone", "name": "iPhone", "source": 1, "walkout_enabled": False},
    ])
    state = State()
    path = tmp_path / "setup.yaml"
    session = WebSession(path, config, load_walkout_config(DEMO_CONFIG), state, tmp_path, None, False)
    app = create_app(state, tmp_path, None)
    install_web_routes(app, session)
    closed = []

    class Camera:
        live = False

        def first_frame(self):
            return np.full((80, 160, 3), 140, np.uint8)

        def close(self):
            closed.append(True)

    def monitor(config, defaults, **runtime):
        assert len(closed) == 2
        assert config.cameras[0].layout(defaults).dining_zone[0] == (0.1, 0.1)
        assert not config.cameras[1].walkout_enabled
        assert runtime["headless"]
        runtime["state"].publish(b"test frame", [], 0, 0, 0)
        assert runtime["stop"].wait(timeout=3)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        def wait_for(status: str) -> None:
            deadline = monotonic() + 3
            while monotonic() < deadline:
                if client.get("/api/status").json()["status"] == status:
                    return
                sleep(0.01)
            raise AssertionError(f"Dashboard never reported {status!r}")

        try:
            settings = client.get("/api/setup").json()
            assert client.post("/api/session/stop", headers={"Origin": "https://example.org"}).status_code == 403
            with patch("survai.web.open_calibration_capture", side_effect=lambda *_: Camera()):
                response = client.post("/api/session/preview", json={"cameras": settings["cameras"]})
                assert response.status_code == 202
                deadline = monotonic() + 3
                while client.get("/api/session").json()["previews"].get("phone") != "Ready":
                    assert monotonic() < deadline
                    sleep(0.01)
                frame = client.get("/api/session/preview/phone")
                assert frame.status_code == 200
                assert cv2.imdecode(np.frombuffer(frame.content, np.uint8), cv2.IMREAD_COLOR).shape == (80, 160, 3)
                assert client.post("/api/session/start", json={}).status_code == 409

                corners = [[.1, .1], [.9, .1], [.9, .9], [.1, .9], [.45, .5]]
                settings["calibration"] = {"landmarks": [{"mac": point, "phone": point} for point in corners]}
                settings["cameras"][0]["zones"]["dining_zone"] = corners[:4]
                assert client.put("/api/setup", json=settings).status_code == 204
                assert yaml.safe_load(path.read_text())["calibration"]["landmarks"] == settings["calibration"]["landmarks"]
                assert client.post("/api/session/stop").status_code == 202
                wait_for("Monitoring stopped")
            with patch("survai.multi_camera.run_multi", side_effect=monitor):
                assert client.post("/api/session/start", json={}).status_code == 202
                wait_for("Monitoring")
                assert client.post("/api/session/pay").status_code == 202
                assert session.pay.is_set()
                assert client.post("/api/session/stop").status_code == 202
                wait_for("Monitoring stopped")
        finally:
            session.close()


def test_paused_camera_is_retained_but_never_opened(tmp_path: Path) -> None:
    config = MultiCameraConfig(cameras=[
        {"id": "mac", "name": "MacBook", "source": 0, "walkout_enabled": False, "employee_marker": True},
        {"id": "phone", "name": "iPhone", "source": 1, "enabled": False},
    ])
    state = State()
    session = WebSession(tmp_path / "setup.yaml", config, load_walkout_config(DEMO_CONFIG),
                         state, tmp_path, None, False)
    app = create_app(state, tmp_path, None)
    install_web_routes(app, session)

    def preview(cameras):
        assert [camera.id for camera in cameras] == ["mac"]
        assert session.stop.wait(timeout=3)

    def monitor(runtime_config, defaults, **runtime):
        assert [camera.id for camera in runtime_config.cameras] == ["mac"]
        assert runtime_config.cameras[0].employee_marker
        state.publish(b"test", [], 0, 0, 0)
        assert runtime["stop"].wait(timeout=3)

    def wait_for(client, predicate):
        deadline = monotonic() + 3
        while not predicate():
            assert monotonic() < deadline
            sleep(0.01)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        setup = client.get("/api/setup").json()
        assert setup["cameras"][1]["enabled"] is False
        assert client.get("/employee-marker.png").content.startswith(b"\x89PNG")
        try:
            with patch.object(session, "run_preview", side_effect=preview) as preview_call:
                assert client.post("/api/session/preview", json={"cameras": setup["cameras"]}).status_code == 202
                wait_for(client, lambda: preview_call.called)
                assert client.post("/api/session/stop").status_code == 202
                wait_for(client, lambda: client.get("/api/session").json()["mode"] == "idle")
            with patch("survai.multi_camera.run_multi", side_effect=monitor):
                assert client.post("/api/session/start", json={}).status_code == 202
                wait_for(client, lambda: client.get("/api/status").json()["status"] == "Monitoring")
                assert client.get("/api/status").json()["walkout_enabled"] is False
        finally:
            session.close()


def test_doorway_activity_preserves_event_totals_in_newest_first_order(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from survai.doorway import Crossing
    from survai.state import DoorActivity

    state = State()
    stamp = datetime(2026, 11, 14, 20, 41, 5, tzinfo=timezone.utc)
    state.publish_door_counts(9, 6, 1, [
        DoorActivity(Crossing(7, "came in", 9, 5), stamp, False, 2),
        DoorActivity(Crossing(3, "left", 9, 6), stamp, True, 1),
    ])
    with TestClient(create_app(state, tmp_path, None)) as client:
        status = client.get("/api/status").json()
    assert status["door_counts"] == {"in_store": 3, "came_in": 9, "left": 6, "employees": 1}
    employee_exit, customer_entry = status["door_activity"]
    assert employee_exit["employee"] and employee_exit["crossing"]["direction"] == "left"
    assert customer_entry == {
        "crossing": {"track_id": 7, "direction": "came in", "came_in": 9, "left": 5},
        "timestamp": "2026-11-14T20:41:05Z",
        "employee": False,
        "employees": 2,
        "in_store": 4,
    }
