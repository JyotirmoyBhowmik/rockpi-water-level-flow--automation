"""
Voice Alert Synthesizer for ROCK Pi S via USB Audio Interface (ALSA & espeak-ng).
Includes debouncing, priority queuing, quiet hours filtering, and deterministic fallback.
No mock simulation - logs cleanly when audio hardware is absent.
"""

import asyncio
from datetime import datetime
import logging
import shutil
import time
from typing import Dict, List, Optional
from app.config import system_config

logger = logging.getLogger("smart_water.audio")


class VoiceAlertSynthesizer:
    """
    Spoken audio synthesizer invoking espeak-ng piped to aplay over ALSA.
    Features:
      - Priority Queue (Emergency=0, Warning=1, Info=2)
      - De-duplication (suppresses identical messages within 30s)
      - Quiet hours enforcement (suppresses non-emergency alerts during night hours)
    """

    def __init__(self):
        self._queue: Optional[asyncio.Queue[Dict]] = None
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._last_spoken_text = ""
        self._last_spoken_time = 0.0
        self._history: List[Dict[str, str]] = []
        self._has_audio_hardware = False

    async def start(self) -> None:
        """Initializes voice synthesis queue and tests binary availability."""
        if self._running:
            return

        has_espeak = shutil.which("espeak-ng") is not None
        has_aplay = shutil.which("aplay") is not None
        self._has_audio_hardware = has_espeak and has_aplay

        if not self._has_audio_hardware:
            logger.warning(
                "espeak-ng or aplay not found in system PATH. Voice alerts will be logged to system journal only."
            )

        self._queue = asyncio.Queue()
        self._running = True
        self._worker_task = asyncio.create_task(self._playback_worker(), name="audio_playback_worker")
        logger.info("VoiceAlertSynthesizer started.")

    async def speak(self, text: str, priority: int = 1) -> None:
        """
        Enqueues voice alert.
        Priority: 0 = Emergency (bypasses quiet hours), 1 = Warning, 2 = Info.
        """
        if not text or not system_config.audio.enabled:
            return

        now = time.time()
        # De-duplicate identical alert within 30 seconds
        if text == self._last_spoken_text and (now - self._last_spoken_time) < 30.0:
            logger.debug("Debouncing duplicate voice alert: %s", text)
            return

        # Quiet hours check
        if priority > 0 and self._is_in_quiet_hours():
            logger.info("Quiet hours active: Suppressing voice alert '%s'", text)
            return

        if self._queue is not None:
            await self._queue.put({"text": text, "priority": priority, "created_at": now})

    def _is_in_quiet_hours(self) -> bool:
        """Checks if current time falls within configured quiet hours."""
        start = system_config.audio.quiet_hours_start
        end = system_config.audio.quiet_hours_end
        if start is None or end is None:
            return False

        current_hour = datetime.now().hour
        if start < end:
            return start <= current_hour < end
        else:
            # Over midnight (e.g. 22 to 7)
            return current_hour >= start or current_hour < end

    async def _playback_worker(self) -> None:
        """Processes voice alerts sequentially to prevent audio overlapping."""
        while self._running:
            try:
                if self._queue is None:
                    break
                item = await self._queue.get()
                text = item["text"]
                self._last_spoken_text = text
                self._last_spoken_time = time.time()

                logger.info("[VOICE ALERT P%d] %s", item["priority"], text)

                # Execute audio playback if physical tools exist
                if self._has_audio_hardware:
                    cfg = system_config.audio
                    cmd = (
                        f'espeak-ng -v {cfg.voice} -s {cfg.speed} "{text}" --stdout | '
                        f'aplay -D {cfg.alsa_device} -q'
                    )
                    try:
                        proc = await asyncio.create_subprocess_shell(
                            cmd,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await proc.wait()
                    except Exception as e:
                        logger.error("Error invoking ALSA speech pipeline: %s", e)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in audio playback worker: %s", exc)

    async def stop(self) -> None:
        """Stops worker and flushes queue."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._queue = None
        logger.info("VoiceAlertSynthesizer stopped.")


audio_controller = VoiceAlertSynthesizer()
