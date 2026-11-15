"""Local WhatsApp settings and Twilio delivery; no images leave the computer."""

import json
import logging
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

import httpx
from dotenv import dotenv_values, set_key
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from ..events import Event

LOG = logging.getLogger(__name__)
SETTINGS_LOCK = RLock()
ENV_FIELDS = {
    "enabled": "WHATSAPP_ENABLED",
    "account_sid": "TWILIO_ACCOUNT_SID",
    "auth_token": "TWILIO_AUTH_TOKEN",
    "sender": "TWILIO_WHATSAPP_FROM",
    "recipient": "TWILIO_WHATSAPP_TO",
    "content_sid": "TWILIO_WHATSAPP_CONTENT_SID",
}


class WhatsAppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    account_sid: str = ""
    auth_token: SecretStr = SecretStr("")
    sender: str = ""
    recipient: str = ""
    content_sid: str = ""

    @field_validator("account_sid", "content_sid")
    @classmethod
    def valid_sid(cls, value: str, info) -> str:
        value = value.strip()
        prefix = "AC" if info.field_name == "account_sid" else "HX"
        if value and not re.fullmatch(prefix + r"[0-9a-fA-F]{32}", value):
            raise ValueError(f"Enter a {prefix} SID followed by 32 hexadecimal characters.")
        return value

    @field_validator("sender", "recipient")
    @classmethod
    def valid_phone(cls, value: str) -> str:
        value = value.strip().removeprefix("whatsapp:")
        if value and not re.fullmatch(r"\+[1-9][0-9]{6,14}", value):
            raise ValueError("Enter a phone number with + and country code, such as +15551234567.")
        return value

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token.get_secret_value() and self.sender and self.recipient)

    def public(self) -> dict[str, object]:
        return self.model_dump(exclude={"auth_token"}) | {
            "has_auth_token": bool(self.auth_token.get_secret_value()), "configured": self.configured,
        }


def load_settings(path: Path, fallback_enabled: bool = False) -> WhatsAppSettings:
    with SETTINGS_LOCK:
        # Saved UI settings take priority; environment variables remain a fallback for CLI use.
        values = dict(os.environ) | {key: value for key, value in dotenv_values(path).items() if value is not None}
        fields = {field: values[key] for field, key in ENV_FIELDS.items() if key in values}
        fields.setdefault("enabled", fallback_enabled)
        return WhatsAppSettings.model_validate(fields)


def save_settings(path: Path, settings: WhatsAppSettings, fallback_enabled: bool = False) -> WhatsAppSettings:
    with SETTINGS_LOCK:
        if not settings.auth_token.get_secret_value():
            settings = settings.model_copy(update={"auth_token": load_settings(path, fallback_enabled).auth_token})
        if settings.enabled and not settings.configured:
            raise ValueError("Add your Twilio Account SID, Auth Token, sender number, and recipient number first.")
        # Preserve unrelated .env values and replace atomically so camera threads never read half a save.
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".whatsapp-", delete=False) as file:
            temporary = Path(file.name)
            if path.exists():
                file.write(path.read_text(encoding="utf-8"))
        try:
            for field, key in ENV_FIELDS.items():
                value = getattr(settings, field)
                if isinstance(value, SecretStr):
                    value = value.get_secret_value()
                elif isinstance(value, bool):
                    value = str(value).lower()
                set_key(temporary, key, value)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return settings


class WhatsAppDeliveryError(RuntimeError):
    pass


def provider_error(code: object, status: int | None = None) -> str:
    code = int(str(code)) if str(code).isdecimal() else None
    hints = {
        20003: "Check your Twilio Account SID and Auth Token.",
        63007: "Check that the sender is your Twilio WhatsApp sender or Sandbox number.",
        63015: "Join your Twilio WhatsApp Sandbox from the recipient phone first.",
        63016: "Reply to your Twilio WhatsApp number to open the 24-hour window, or use an approved template.",
        21608: "Verify the recipient number in your Twilio trial account.",
    }
    # Never return the provider's raw body, which may contain phone numbers or credentials.
    label = f"Twilio error {code}" if isinstance(code, int) else f"Twilio HTTP {status}" if status else "Twilio error"
    return f"{label}. {hints.get(code, 'Check the message in your Twilio Messaging logs.')}"


def send_message(settings: WhatsAppSettings, body: str) -> dict[str, str]:
    if not settings.configured:
        raise WhatsAppDeliveryError("Save your Twilio credentials and WhatsApp numbers first.")
    data = {"From": "whatsapp:" + settings.sender, "To": "whatsapp:" + settings.recipient}
    if settings.content_sid:
        data.update(ContentSid=settings.content_sid, ContentVariables=json.dumps({"1": " ".join(body.split())}))
    else:
        data["Body"] = body
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.account_sid}/Messages.json",
            auth=(settings.account_sid, settings.auth_token.get_secret_value()), data=data, timeout=10,
        )
    except httpx.RequestError:
        raise WhatsAppDeliveryError("Could not reach Twilio. Check the computer's internet connection.") from None
    try:
        result = response.json()
    except ValueError:
        raise WhatsAppDeliveryError("Twilio returned an unreadable response. Check its Messaging logs.") from None
    if not isinstance(result, dict):
        raise WhatsAppDeliveryError("Twilio returned an unexpected response.")
    if not response.is_success or result.get("status") in {"failed", "undelivered"}:
        raise WhatsAppDeliveryError(provider_error(result.get("code") or result.get("error_code"), response.status_code))
    if not isinstance(result.get("sid"), str) or not result["sid"]:
        raise WhatsAppDeliveryError("Twilio did not return a message ID. Check its Messaging logs.")
    return {"message_sid": result["sid"], "status": str(result.get("status", "accepted"))}


class WhatsAppSink:
    def __init__(self, enabled: bool = True, *, settings_path: Path | None = None) -> None:
        self.fallback_enabled = enabled
        self.settings_path = settings_path or Path.cwd() / ".env"

    def send(self, event: Event) -> None:
        try:
            settings = load_settings(self.settings_path, self.fallback_enabled)
            if not settings.enabled:
                return
            label = {"possible_walkout": "Possible walk-out", "counter_wait": "Customer waiting at counter"}.get(
                event.type, event.type.replace("_", " ").capitalize(),
            )
            guests = ", ".join(f"Guest #{track_id}" for track_id in event.track_ids)
            body = "\n".join(part for part in ("Surveillance.ai", label, guests, event.message, event.timestamp) if part)
            if event.snapshot_path or event.clip_path:
                body += "\nReview evidence in the local dashboard."
            send_message(settings, body)
        except (WhatsAppDeliveryError, ValueError, OSError) as error:
            # Pydantic errors can include their inputs. Log only a safe configuration message for those.
            detail = str(error) if isinstance(error, WhatsAppDeliveryError) else "Check the local WhatsApp settings."
            LOG.warning("WhatsApp alert was not accepted. %s", detail)
        else:
            LOG.info("WhatsApp alert accepted by Twilio. Check Twilio logs for delivery status.")
