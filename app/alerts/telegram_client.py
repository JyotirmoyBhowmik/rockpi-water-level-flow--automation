"""
Telegram Bot Notification Client for Critical IoT Alerts.
Includes bounded offline queue, exponential backoff retries, and zero-token default.
"""

import asyncio
import logging
from typing import Optional
import httpx
from app.config import system_config

logger = logging.getLogger("smart_water.telegram")


class TelegramNotifier:
    """
    Asynchronous Telegram notification agent.
    Enqueues alerts to a bounded buffer (max 50) and delivers with backoff retries.
    """

    def __init__(self, max_queue: int = 50):
        self._queue: Optional[asyncio.Queue[str]] = None
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._max_queue = max_queue

    async def start(self) -> None:
        if self._running:
            return
        if not system_config.telegram.enabled:
            logger.info("Telegram alerting is disabled in configuration.")
            return

        self._queue = asyncio.Queue(maxsize=self._max_queue)
        self._running = True
        self._worker_task = asyncio.create_task(self._delivery_worker(), name="telegram_worker")
        logger.info("Telegram notification worker started.")

    async def send_alert(self, event_type: str, message: str) -> None:
        """Enqueues an alert message for transmission."""
        if not self._running or self._queue is None:
            return

        formatted = f"🚨 *[SMART WATER ALERT]*\n*Event:* `{event_type}`\n*Message:* {message}"
        try:
            self._queue.put_nowait(formatted)
        except asyncio.QueueFull:
            logger.warning("Telegram queue full (dropping oldest alert).")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(formatted)
            except Exception:
                pass

    async def _delivery_worker(self) -> None:
        token = system_config.telegram.bot_token
        chat_id = system_config.telegram.chat_id

        if not token or not chat_id:
            logger.warning("Telegram enabled but bot_token or chat_id is null. Alerts suppressed.")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        while self._running:
            try:
                if self._queue is None:
                    break
                text = await self._queue.get()

                # Attempt delivery with exponential backoff
                backoff = 2.0
                delivered = False
                for attempt in range(4):
                    try:
                        async with httpx.AsyncClient(timeout=6.0) as client:
                            resp = await client.post(
                                url,
                                json={
                                    "chat_id": chat_id,
                                    "text": text,
                                    "parse_mode": "Markdown",
                                },
                            )
                            if resp.status_code == 200:
                                delivered = True
                                break
                            else:
                                logger.error("Telegram API rejected message (status %d): %s", resp.status_code, resp.text)
                    except Exception as net_err:
                        logger.warning("Telegram delivery attempt %d failed: %s. Retrying in %.1fs", attempt + 1, net_err, backoff)
                        await asyncio.sleep(backoff)
                        backoff *= 2.0

                if not delivered:
                    logger.error("Failed to deliver Telegram alert after retries. Discarded.")

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in Telegram delivery loop: %s", exc)
                await asyncio.sleep(5.0)

    async def stop(self) -> None:
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._queue = None


telegram_notifier = TelegramNotifier()
