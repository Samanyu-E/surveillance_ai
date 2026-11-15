import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from survai.alerts import notify
from survai.events import Event
from survai.server import create_app
from survai.state import State


@pytest.mark.parametrize("native_sound,fails", [(True, False), (True, True), (False, False)])
def test_windows_sound_and_delivery(
    monkeypatch: pytest.MonkeyPatch, native_sound: bool, fails: bool,
) -> None:
    beep = Mock(side_effect=RuntimeError("No speaker") if fails else None)
    monkeypatch.setattr(notify, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(__import__("sys").modules, "winsound", SimpleNamespace(
        MessageBeep=beep, MB_ICONEXCLAMATION=48,
    ))
    notifier = notify.AlertNotifier(False, native_sound=native_sound)
    notifier.sink = Mock()
    event = Event("counter_wait", "warning", (7,), "", "Waiting", 5)
    try:
        notifier._deliver(event)
        assert beep.call_count == int(native_sound)
        notifier.sink.send.assert_called_once_with(event)
    finally:
        notifier.close()


def test_event_stream_marks_end_of_history_before_live_alerts(tmp_path: Path) -> None:
    async def read_stream() -> None:
        state = State()
        app = create_app(state, tmp_path, summary_model=None)
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/events")

        async def connected() -> bool:
            return False

        saved = Event("possible_walkout", "warning", (1,), "", "Old", 0)
        state.publish(b"", [saved], 0, 1, 0)
        response = await endpoint(SimpleNamespace(headers={}, is_disconnected=connected))
        stream = response.body_iterator
        try:
            assert await anext(stream) == ": connected\n\n"
            assert saved.id in await anext(stream)
            assert await anext(stream) == "event: ready\ndata: {}\n\n"
            new = Event("counter_wait", "warning", (2,), "", "New", 5)
            state.publish(b"", [new], 5, 2, 0)
            assert new.id in await asyncio.wait_for(anext(stream), 1)
        finally:
            await stream.aclose()

    asyncio.run(read_stream())
