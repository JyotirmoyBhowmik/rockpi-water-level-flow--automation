"""
Voice Synthesizer Driver using ALSA and espeak-ng over USB Audio Interface.
Includes debounced queuing, rate-limiting, and automatic mock fallback.
"""

import asyncio
import logging
import shutil
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.config import settings
from app.hardware.base import BaseAudioController
from app.hardware.mock import mock_audio

logger = logging.getLogger("smart_water.audio")


class RealAudioDriver(BaseAudioController):
    """
    Physical voice alert synthesizer invoking espeak-ng piped to aplay.
    """

    def __init__(
        self,
        voice: str = settings.ESPEAK_VOICE,
        speed: int = settings.ESPEAK_SPEED,
        alsa_dev: str = settings.ALSA_DEVICE,
        max_history: int = 50,
    ):
        self.voice = voice
        self.speed = speed
        self.alsa_dev = alsa_dev
        self.max_history = max_history
        self._queue: asyncio.Queue[Dict] = asyncio.Queue()
        self._history: List[Dict[str, str]] = []
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._last_spoken_text = ""
        self._last_spoken_time = 0.0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._playback_worker(), name="audio_playback_worker")
        logger.info("RealAudioDriver initialized. Speech queue worker started.")

    async def speak(self, text: str, priority: int = 1) -> None:
        """
        Enqueues announcement. Debounces identical messages within 30 seconds.
        """
        if not text or not settings.AUDIO_ENABLED:
            return

        now = time.time()
        if text == self._last_spoken_text and (now - self._last_spoken_time) < 30.0:
            logger.debug("Debouncing duplicate voice alert: %s", text)
            return

        await self._queue.put({"text": text, "priority": priority, "created_at": now})

    async def _playback_worker(self) -> None:
        while self._running:
            try:
                item = await self._queue.get()
                text = item["text"]
                self._last_spoken_text = text
                self._last_spoken_time = time.time()

                ts = datetime.now(timezone.utc).isoformat()
                self._history.append({"timestamp": ts, "text": text, "priority": str(item["priority"])})
                if len(self._history) > self.max_history:
                    self._history.pop(0)

                # Execute espeak-ng pipeline asynchronously:
                # espeak-ng -v en-us -s 155 "{text}" --stdout | aplay -D default -q
                cmd = f'espeak-ng -v {self.voice} -s {self.speed} "{text}" --stdout | aplay -D {self.alsa_dev} -q'
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error synthesizing voice alert: %s", e)

    async def stop(self) -> None:
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("RealAudioDriver stopped.")

    def get_recent_speech_logs(self, limit: int = 10) -> List[Dict[str, str]]:
        return list(reversed(self._history[-limit:]))


def get_audio_controller() -> BaseAudioController:
    """Factory selecting physical ALSA voice driver or virtual mock."""
    has_espeak = shutil.which("espeak-ng") is not None
    has_aplay = shutil.which("aplay") is not None

    if settings.MOCK_HARDWARE or not has_espeak or not has_aplay:
        if not settings.MOCK_HARDWARE:
            logger.warning("espeak-ng or aplay not found in PATH. Using MockAudioController.")
        return mock_audio
    return RealAudioDriver()


audio_controller = get_audio_controller()
