"""A tiny thread-safe publish/subscribe hub feeding Server-Sent Events."""

from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    """Fan-out of ``(event name, JSON-able data)`` pairs to any number of listeners."""

    def __init__(self, max_queue: int = 1000):
        self._max = max_queue
        self._listeners: list[queue.Queue[tuple[str, Any]]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[tuple[str, Any]]:
        q: queue.Queue[tuple[str, Any]] = queue.Queue(self._max)
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[tuple[str, Any]]) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def publish(self, name: str, data: Any) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for q in listeners:
            try:
                q.put_nowait((name, data))
            except queue.Full:  # a stalled browser tab must never block the engine
                self.unsubscribe(q)

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)
