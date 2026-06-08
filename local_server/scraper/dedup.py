import hashlib

import redis.asyncio as redis
from local_server.config import get_settings

settings = get_settings()

class Deduplicator:
    def __init__(self):
        self.redis = redis.from_url(settings.redis_url)
        self.ttl = settings.redis_dedup_ttl

    def _url_key(self, url: str) -> str:
        return f"dedup:url:{hashlib.md5(url.encode('utf-8')).hexdigest()}"

    def _title_key(self, title: str) -> str:
        normalized = title.lower().strip()[:120]
        return f"dedup:title:{hashlib.md5(normalized.encode('utf-8')).hexdigest()}"

    async def is_duplicate(self, url: str, title: str) -> bool:
        if not url and not title:
            return False
        exists = await self.redis.exists(self._url_key(url)) or await self.redis.exists(self._title_key(title))
        return bool(exists)

    async def mark_seen(self, url: str, title: str) -> None:
        if url:
            await self.redis.setex(self._url_key(url), self.ttl, 1)
        if title:
            await self.redis.setex(self._title_key(title), self.ttl, 1)
