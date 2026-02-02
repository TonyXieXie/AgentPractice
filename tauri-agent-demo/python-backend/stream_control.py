import asyncio
from typing import Dict, Optional


class StreamStopRegistry:
    def __init__(self) -> None:
        self._events: Dict[int, asyncio.Event] = {}

    def create(self, key: int) -> asyncio.Event:
        event = asyncio.Event()
        self._events[key] = event
        return event

    def get(self, key: int) -> Optional[asyncio.Event]:
        return self._events.get(key)

    def stop(self, key: int) -> bool:
        event = self._events.get(key)
        if not event:
            return False
        event.set()
        return True

    def clear(self, key: int) -> None:
        self._events.pop(key, None)


stream_stop_registry = StreamStopRegistry()
