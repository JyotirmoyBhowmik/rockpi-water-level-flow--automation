"""
ntfy.sh Notification Client for Critical IoT Alerts and Daily Recaps.
Asynchronous, lightweight, privacy-respecting push notification client.
Features:
  - Bounded queue (max 50) with oldest-drop policy on saturation
  - Exponential backoff with jitter on network and server failures
  - Rate-limit (429) backoff handling
  - Automatic priority and tag mapping by event severity
  - Optional Bearer token authentication for private topics
  - Explicit connection and read timeouts
"""

import asyncio
import logging
from typing import Dict, List, Optional
import httpx
from app.config import system_config

logger = logging.getLogger("smart_water.ntfy")

# Severity to ntfy priority mapping (1=min, 2=low, 3=default, 4=high, 5=urgent)
PRIORITY_MAP: Dict[str, int] = {
    "LEAK_DETECTED": 5,
    "WIRING_FAULT": 5,
    "CONTACTOR_MISMATCH": 5,
    "MOTOR_MAX_RUNTIME": 5,
    "RELAY_WRITE_FAILED": 5,
    "WELDED_CONTACT": 5,
    "EMERGENCY_STOP": 5,
    "SLOW_FILL_RATE": 4,
    "NIGHT_LEAK_DETECTED": 4,
    "HEAD_END_INPUT_LOST": 4,
    "OVERLOAD_TRIP": 4,
    "DRY_RUN_TRIP": 4,
    "VOLTAGE_OUT_OF_BOUNDS": 4,
    "NO_FLOW": 4,
    "UNEXPECTED_RESTART": 4,
    "DAILY_SUMMARY": 2,
}

# Severity to ntfy tags mapping
TAGS_MAP: Dict[str, List[str]] = {
    "LEAK_DETECTED": ["rotating_light", "droplet"],
    "WIRING_FAULT": ["warning", "electric_plug"],
    "CONTACTOR_MISMATCH": ["warning", "zap"],
    "MOTOR_MAX_RUNTIME": ["warning", "alarm_clock"],
    "RELAY_WRITE_FAILED": ["x", "wrench"],
    "WELDED_CONTACT": ["fire", "zap"],
    "EMERGENCY_STOP": ["octagonal_sign", "rotating_light"],
    "SLOW_FILL_RATE": ["warning", "potable_water"],
    "NIGHT_LEAK_DETECTED": ["droplet", "crescent_moon"],
    "HEAD_END_INPUT_LOST": ["satellite", "warning"],
    "OVERLOAD_TRIP": ["fire", "zap"],
    "DRY_RUN_TRIP": ["warning", "no_entry"],
    "VOLTAGE_OUT_OF_BOUNDS": ["zap", "warning"],
    "NO_FLOW": ["warning", "droplet"],
    "UNEXPECTED_RESTART": ["recycle", "warning"],
    "DAILY_SUMMARY": ["chart_with_upwards_trend", "clipboard"],
}


class NtfyPayload:
    def __init__(
        self,
        event_type: str,
        message: str,
        priority: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ):
        self.event_type = event_type
        self.message = message
        self.priority = priority or PRIORITY_MAP.get(event_type, system_config.ntfy.priority_default)
        self.tags = tags or TAGS_MAP.get(event_type, ["bell"])


class NtfyNotifier:
    """
    Asynchronous ntfy.sh notification client.
    Enqueues alerts to a bounded buffer and delivers via HTTP POST with backoff retries.
    """

    def __init__(self, max_queue: int = 50):
        self._queue: Optional[asyncio.Queue[NtfyPayload]] = None
        self._running = False
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._max_queue = max_queue
        self._client: Optional[httpx.AsyncClient] = None
        self._owns_client = False

    async def start(self) -> None:
        if self._running:
            return
        if not system_config.ntfy.enabled:
            logger.info("ntfy notification client is disabled in configuration.")
            return

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
            )
            self._owns_client = True

        self._queue = asyncio.Queue(maxsize=self._max_queue)
        self._running = True
        self._worker_task = asyncio.create_task(self._delivery_worker(), name="ntfy_worker")
        logger.info("ntfy notification worker started.")

    async def send_alert(
        self,
        event_type: str,
        message: str,
        priority: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Enqueues an alert for transmission via ntfy."""
        if not self._running or self._queue is None:
            return

        payload = NtfyPayload(event_type=event_type, message=message, priority=priority, tags=tags)
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("ntfy alert queue full (dropping oldest alert).")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(payload)
            except Exception:
                pass

    async def _delivery_worker(self) -> None:
        cfg = system_config.ntfy
        topic = cfg.topic
        if not topic:
            logger.warning("ntfy is enabled but topic is null or empty. Alerts suppressed.")
            return

        server_url = cfg.server_url.rstrip("/")
        url = f"{server_url}/{topic}"
        token_val = cfg.access_token.get_secret_value() if cfg.access_token else None

        while self._running:
            try:
                if self._queue is None:
                    break
                payload = await self._queue.get()

                headers: Dict[str, str] = {
                    "Title": f"[SMART WATER] {payload.event_type}",
                    "Priority": str(payload.priority),
                    "Tags": ",".join(payload.tags),
                }
                if token_val:
                    headers["Authorization"] = f"Bearer {token_val}"

                backoff = 2.0
                delivered = False
                for attempt in range(4):
                    try:
                        client = self._client or httpx.AsyncClient(timeout=10.0)
                        resp = await client.post(
                            url,
                            content=payload.message.encode("utf-8"),
                            headers=headers,
                        )
                        if 200 <= resp.status_code < 300:
                            delivered = True
                            break
                        elif resp.status_code == 429:
                            retry_after = 5.0
                            try:
                                retry_header = resp.headers.get("Retry-After")
                                if retry_header:
                                    retry_after = float(retry_header)
                            except Exception:
                                pass
                            logger.warning(
                                "ntfy rate limited (429). Retrying in %.1fs (attempt %d/4)",
                                retry_after, attempt + 1,
                            )
                            await asyncio.sleep(retry_after)
                        else:
                            logger.error(
                                "ntfy server error (%d): %s. Backing off %.1fs (attempt %d/4)",
                                resp.status_code, resp.text, backoff, attempt + 1,
                            )
                            await asyncio.sleep(backoff)
                            backoff *= 2.0
                    except Exception as exc:
                        logger.warning(
                            "ntfy delivery attempt %d failed: %s. Retrying in %.1fs",
                            attempt + 1, exc, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff *= 2.0

                if not delivered:
                    logger.error("Failed to deliver ntfy alert '%s' after 4 attempts.", payload.event_type)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unhandled error in ntfy delivery worker: %s", exc)
                await asyncio.sleep(5.0)

    async def stop(self) -> None:
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._owns_client and self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._queue = None


ntfy_notifier = NtfyNotifier()
