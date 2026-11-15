import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..events import Event
from . import AlertSink, ConsoleSink, WhatsAppSink

LOG = logging.getLogger(__name__)


class AlertNotifier:
    def __init__(self, whatsapp: bool, *, native_sound: bool = True, settings_path: Path | None = None) -> None:
        self.console = ConsoleSink()
        self.sink: AlertSink = WhatsAppSink(whatsapp, settings_path=settings_path)
        self.native_sound = native_sound
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alerts")

    def notify(self, event: Event) -> None:
        self.console.send(event)
        self.worker.submit(self._deliver, event)

    def _deliver(self, event: Event) -> None:
        if self.native_sound:
            try:
                if sys.platform == "win32":
                    import winsound

                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                elif sys.platform == "darwin":
                    with subprocess.Popen(
                        ["/usr/bin/afplay", "/System/Library/Sounds/Glass.aiff"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    ) as player:
                        try:
                            player.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            player.kill()
                            raise
            except (ImportError, OSError, RuntimeError, subprocess.TimeoutExpired):
                LOG.debug("Could not play the alert sound", exc_info=True)
        self.sink.send(event)

    def close(self) -> None:
        self.worker.shutdown()
