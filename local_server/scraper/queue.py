import json
from datetime import datetime

import redis.asyncio as redis
from local_server.config import get_settings

settings = get_settings()

class RawArticleQueue:
    def __init__(self):
        self.redis = redis.from_url(settings.redis_url)
        # Blocking commands need socket_timeout=0 (no timeout) so the client
        # doesn't raise TimeoutError while waiting for BLPOP to return.
        self._blocking_redis = redis.from_url(settings.redis_url, socket_timeout=0)
        self.queue_name = settings.redis_raw_queue

    async def push(self, article: dict) -> None:
        article["queued_at"] = article.get("queued_at") or datetime.utcnow().isoformat()
        await self.redis.rpush(self.queue_name, json.dumps(article, ensure_ascii=False))

    async def pop(self) -> dict | None:
        raw = await self.redis.lpop(self.queue_name)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def blpop(self, timeout: float = 5.0) -> dict | None:
        """Blocking pop — waits up to `timeout` seconds for an item."""
        result = await self._blocking_redis.blpop([self.queue_name], timeout=timeout)
        if not result:
            return None
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def length(self) -> int:
        return await self.redis.llen(self.queue_name)
