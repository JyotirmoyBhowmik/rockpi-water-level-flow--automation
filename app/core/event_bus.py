"""
Lightweight asynchronous in-memory Event Bus.
Zero external broker requirement (No Redis/RabbitMQ), strictly optimized for RK3308 RAM.
"""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Set
from datetime import datetime, timezone

logger = logging.getLogger("smart_water.event_bus")

EventHandler = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """
    Lightweight Pub/Sub Event Bus with topic support, bounded processing,
    and isolated failure handling.
    """

    def __init__(self, max_queue_size: int = 100) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: Dict[str, Set[EventHandler]] = {}
        self._wildcard_subscribers: Set[EventHandler] = set()
        self._queue: asyncio.Queue[tuple[str, Dict[str, Any]]] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._running: bool = False

    async def start(self) -> None:
        """Starts the background event processor."""
        if self._running:
            return
        self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._running = True
        self._worker_task = asyncio.create_task(self._process_queue(), name="event_bus_worker")
        logger.info("EventBus started background dispatch worker.")

    async def stop(self) -> None:
        """Gracefully stops the event bus and flushes remaining queue items."""
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._queue = None
        logger.info("EventBus stopped.")

    def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe an async callback to a topic or '*' for all topics."""
        if topic == "*":
            self._wildcard_subscribers.add(handler)
        else:
            if topic not in self._subscribers:
                self._subscribers[topic] = set()
            self._subscribers[topic].add(handler)

    def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Unsubscribe a callback from a topic."""
        if topic == "*":
            self._wildcard_subscribers.discard(handler)
        elif topic in self._subscribers:
            self._subscribers[topic].discard(handler)

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        """
        Publish an event to a topic.
        Adds timestamp and correlation metadata if not present.
        """
        if not self._running or self._queue is None:
            logger.warning("Attempted to publish event '%s' while EventBus is stopped.", topic)
            return

        enriched_payload = {
            **payload,
            "_timestamp": payload.get("_timestamp") or datetime.now(timezone.utc).isoformat(),
            "_topic": topic,
        }

        try:
            self._queue.put_nowait((topic, enriched_payload))
        except asyncio.QueueFull:
            logger.warning("EventBus queue full (drop oldest). Event dropped: %s", topic)
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((topic, enriched_payload))
            except Exception as e:
                logger.error("Failed to re-enqueue event: %s", e)

    async def _process_queue(self) -> None:
        """Background worker that dispatches queued events to subscribers."""
        while self._running:
            if self._queue is None:
                await asyncio.sleep(0.05)
                continue
            try:
                topic, payload = await self._queue.get()
                handlers: List[EventHandler] = list(self._subscribers.get(topic, set()))
                handlers.extend(self._wildcard_subscribers)

                if handlers:
                    # Execute all handlers concurrently without blocking the queue
                    tasks = [asyncio.create_task(self._safe_execute(h, topic, payload)) for h in handlers]
                    await asyncio.gather(*tasks, return_exceptions=True)

                if self._queue is not None:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unhandled error in EventBus dispatch: %s", exc, exc_info=True)

    async def _safe_execute(self, handler: EventHandler, topic: str, payload: Dict[str, Any]) -> None:
        """Execute a single handler with error isolation."""
        try:
            await handler(topic, payload)
        except Exception as exc:
            logger.error("Handler %s failed on topic '%s': %s", getattr(handler, "__name__", "unknown"), topic, exc)


bus = EventBus()
