import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from survai.alerts import whatsapp
from survai.config import MultiCameraConfig, load_walkout_config
from survai.events import Event
from survai.main import DEMO_CONFIG
from survai.server import create_app
from survai.state import State
from survai.web import WebSession, install_web_routes

TOKEN = "test-token-never-return-this"


def details(**overrides) -> dict:
    return {
        "enabled": True, "account_sid": "AC" + "a" * 32, "auth_token": TOKEN,
        "sender": "+14155238886", "recipient": "+15551234567", "content_sid": "",
    } | overrides


@pytest.fixture(autouse=True)
def isolated_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in whatsapp.ENV_FIELDS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(whatsapp.httpx, "post", Mock(side_effect=AssertionError("Unexpected live delivery")))


@pytest.fixture
def client(tmp_path: Path):
    state = State()
    session = WebSession(
        tmp_path / "setup.yaml", MultiCameraConfig(cameras=[{"id": "cam1", "name": "Camera", "source": 0}]),
        load_walkout_config(DEMO_CONFIG), state, tmp_path, None, False,
    )
    app = create_app(state, tmp_path, None)
    install_web_routes(app, session)
    with TestClient(app, base_url="http://127.0.0.1") as browser:
        yield browser


def accepted(monkeypatch: pytest.MonkeyPatch, **result) -> Mock:
    post = Mock(return_value=httpx.Response(201, json={"sid": "SM" + "c" * 32, "status": "queued"} | result))
    monkeypatch.setattr(whatsapp.httpx, "post", post)
    return post


def test_save_survives_reload_preserves_other_settings_and_never_returns_token(client, tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("UNRELATED_SETTING='keep this'\n", encoding="utf-8")
    response = client.put("/api/whatsapp", json=details())
    assert response.status_code == 200
    assert response.json()["configured"] and response.json()["has_auth_token"]
    assert TOKEN not in response.text
    response = client.get("/api/whatsapp")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["enabled"]
    assert TOKEN not in response.text and "auth_token" not in response.json()
    assert whatsapp.dotenv_values(path)["UNRELATED_SETTING"] == "keep this"
    assert whatsapp.load_settings(path).auth_token.get_secret_value() == TOKEN
    assert not list(tmp_path.glob(".whatsapp-*"))


def test_blank_token_keeps_saved_value_and_saved_disable_overrides_cli_flag(client, tmp_path: Path) -> None:
    assert client.put("/api/whatsapp", json=details()).status_code == 200
    response = client.put("/api/whatsapp", json=details(auth_token="", enabled=False))
    assert response.status_code == 200
    settings = whatsapp.load_settings(tmp_path / ".env", fallback_enabled=True)
    assert not settings.enabled and settings.auth_token.get_secret_value() == TOKEN


def test_missing_credentials_cannot_enable_or_send_and_does_not_save(client, tmp_path: Path) -> None:
    response = client.put("/api/whatsapp", json={"enabled": True})
    assert response.status_code == 422
    assert not (tmp_path / ".env").exists()
    assert client.post("/api/whatsapp/test").status_code == 409
    assert not whatsapp.httpx.post.called


@pytest.mark.parametrize("field,value", [
    ("account_sid", "not-an-account"), ("sender", "555-1234"), ("recipient", "15551234567"),
    ("content_sid", "SM" + "a" * 32),
])
def test_invalid_details_are_rejected_without_leaking_token(client, field: str, value: str) -> None:
    response = client.put("/api/whatsapp", json=details(**{field: value}))
    assert response.status_code == 422 and TOKEN not in response.text


def test_test_button_sends_only_to_saved_recipient_without_enabling_automatic_alerts(client, monkeypatch) -> None:
    post = accepted(monkeypatch)
    assert client.put("/api/whatsapp", json=details(enabled=False)).status_code == 200
    response = client.post("/api/whatsapp/test")
    assert response.status_code == 200 and response.json()["status"] == "queued"
    data = post.call_args.kwargs["data"]
    assert data["To"] == "whatsapp:+15551234567"
    assert data["From"] == "whatsapp:+14155238886"
    assert "test notification" in data["Body"] and "MediaUrl" not in data
    assert post.call_args.kwargs["auth"] == ("AC" + "a" * 32, TOKEN)
    assert not client.get("/api/whatsapp").json()["enabled"]


def test_approved_template_sends_variable_instead_of_freeform_body(client, monkeypatch) -> None:
    post = accepted(monkeypatch)
    assert client.put("/api/whatsapp", json=details(content_sid="HX" + "b" * 32)).status_code == 200
    assert client.post("/api/whatsapp/test").status_code == 200
    data = post.call_args.kwargs["data"]
    assert data["ContentSid"] == "HX" + "b" * 32 and "Body" not in data
    assert "test notification" in json.loads(data["ContentVariables"])["1"]


def test_alert_sender_reloads_enabled_settings_without_restarting_and_keeps_media_local(tmp_path, monkeypatch) -> None:
    post = accepted(monkeypatch)
    path = tmp_path / ".env"
    sink = whatsapp.WhatsAppSink(False, settings_path=path)
    counter = Event("counter_wait", "warning", (7,), "2026-11-15T12:00:00Z", "Waiting five seconds", 5)
    sink.send(counter)
    post.assert_not_called()
    whatsapp.save_settings(path, whatsapp.WhatsAppSettings(**details()))
    sink.send(counter)
    data = post.call_args.kwargs["data"]
    assert "Customer waiting at counter" in data["Body"] and "Guest #7" in data["Body"]
    walkout = Event("possible_walkout", "warning", (8,), "", "Verify payment", 7,
                    snapshot_path="/snapshots/local.jpg", clip_path="/clips/local.mp4")
    sink.send(walkout)
    data = post.call_args.kwargs["data"]
    assert "Possible walk-out" in data["Body"] and "Guest #8" in data["Body"]
    assert "MediaUrl" not in data and "/snapshots/" not in data["Body"]
    whatsapp.save_settings(path, whatsapp.WhatsAppSettings(**details(enabled=False)))
    sink.send(counter)
    assert post.call_count == 2


def test_expired_window_returns_actionable_error_without_provider_details(client, monkeypatch) -> None:
    post = Mock(return_value=httpx.Response(400, json={"code": 63016, "message": TOKEN}))
    monkeypatch.setattr(whatsapp.httpx, "post", post)
    client.put("/api/whatsapp", json=details())
    response = client.post("/api/whatsapp/test")
    assert response.status_code == 502
    assert "24-hour" in response.json()["detail"] and TOKEN not in response.text
    assert post.call_count == 1  # No blind retries that could duplicate a paid message.


def test_network_failure_does_not_crash_monitoring_or_log_secrets(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(whatsapp.httpx, "post", Mock(side_effect=httpx.ConnectError(TOKEN)))
    path = tmp_path / ".env"
    whatsapp.save_settings(path, whatsapp.WhatsAppSettings(**details()))
    whatsapp.WhatsAppSink(False, settings_path=path).send(Event("counter_wait", "warning", (7,), "", "Wait", 5))
    assert "internet connection" in caplog.text and TOKEN not in caplog.text


def test_failed_provider_status_is_not_reported_as_accepted(client, monkeypatch) -> None:
    accepted(monkeypatch, status="failed", error_code=63015)
    client.put("/api/whatsapp", json=details())
    response = client.post("/api/whatsapp/test")
    assert response.status_code == 502 and "Sandbox" in response.json()["detail"]


def test_cross_site_test_request_cannot_send_a_message(client, monkeypatch) -> None:
    post = accepted(monkeypatch)
    client.put("/api/whatsapp", json=details())
    assert client.post("/api/whatsapp/test", headers={"Origin": "https://example.com"}).status_code == 403
    post.assert_not_called()


def test_failed_save_leaves_previous_credentials_intact(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    whatsapp.save_settings(path, whatsapp.WhatsAppSettings(**details()))
    previous = path.read_bytes()
    monkeypatch.setattr(whatsapp, "set_key", Mock(side_effect=OSError("Cannot save")))
    with pytest.raises(OSError):
        whatsapp.save_settings(path, whatsapp.WhatsAppSettings(**details(auth_token="replacement")))
    assert path.read_bytes() == previous and not list(tmp_path.glob(".whatsapp-*"))


def test_existing_environment_credentials_and_whatsapp_prefix_remain_supported(tmp_path, monkeypatch) -> None:
    for field, key in whatsapp.ENV_FIELDS.items():
        if field not in {"enabled", "content_sid"}:
            monkeypatch.setenv(key, str(details()[field]))
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
    settings = whatsapp.load_settings(tmp_path / ".env", fallback_enabled=True)
    assert settings.enabled and settings.configured and settings.sender == "+14155238886"
