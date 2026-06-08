import asyncio
from typing import Any

class EventBroadcaster:
    def __init__(self):
        self.listeners: set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.listeners.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        if not self.listeners:
            return
        for queue in list(self.listeners):
            await queue.put(event)
