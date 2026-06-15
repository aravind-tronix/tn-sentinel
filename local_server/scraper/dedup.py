import hashlib
import re
import time
from difflib import SequenceMatcher

import redis.asyncio as redis
from local_server.config import get_settings

settings = get_settings()

_STOP_WORDS = {
    "a", "an", "the", "in", "of", "and", "or", "to", "is", "are", "was",
    "were", "be", "been", "at", "on", "for", "with", "after", "from", "by",
    "as", "its", "it", "this", "that", "over", "into",
}

_FUZZY_TITLES_KEY = "dedup:titles:fuzzy"
_FUZZY_THRESHOLD = 0.75


def _normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    tokens = [w for w in t.split() if w not in _STOP_WORDS]
    return " ".join(tokens)


class Deduplicator:
    def __init__(self):
        self.redis = redis.from_url(settings.redis_url)
        self.ttl = settings.redis_dedup_ttl

    def _url_key(self, url: str) -> str:
        return f"dedup:url:{hashlib.md5(url.encode('utf-8')).hexdigest()}"

    def _title_key(self, title: str) -> str:
        normalized = title.lower().strip()[:120]
        return f"dedup:title:{hashlib.md5(normalized.encode('utf-8')).hexdigest()}"

    async def _is_fuzzy_duplicate(self, title: str) -> bool:
        normalized = _normalize_title(title)
        if not normalized:
            return False
        cutoff = time.time() - self.ttl
        recent = await self.redis.zrangebyscore(_FUZZY_TITLES_KEY, cutoff, "+inf")
        for entry in recent:
            existing = entry.decode("utf-8")
            if SequenceMatcher(None, normalized, existing).ratio() >= _FUZZY_THRESHOLD:
                return True
        return False

    async def _add_fuzzy_title(self, title: str) -> None:
        normalized = _normalize_title(title)
        if not normalized:
            return
        now = time.time()
        await self.redis.zadd(_FUZZY_TITLES_KEY, {normalized: now})
        # prune entries older than TTL
        await self.redis.zremrangebyscore(_FUZZY_TITLES_KEY, "-inf", now - self.ttl)

    async def is_duplicate(self, url: str, title: str) -> bool:
        if not url and not title:
            return False
        if await self.redis.exists(self._url_key(url)):
            return True
        if title and await self.redis.exists(self._title_key(title)):
            return True
        if title and await self._is_fuzzy_duplicate(title):
            return True
        return False

    async def mark_seen(self, url: str, title: str) -> None:
        if url:
            await self.redis.setex(self._url_key(url), self.ttl, 1)
        if title:
            await self.redis.setex(self._title_key(title), self.ttl, 1)
            await self._add_fuzzy_title(title)
