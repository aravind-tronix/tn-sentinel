import asyncio
import time
from typing import Callable

from .registry import SOURCES
from .extractors.rss_extractor import RSSExtractor
from .extractors.html_extractor import HTMLExtractor
from .queue import RawArticleQueue
from .dedup import Deduplicator

EXTRACTOR_MAP = {
    "rss": RSSExtractor,
    "html": HTMLExtractor,
}

queue = RawArticleQueue()
dedup = Deduplicator()

async def run_source(source: dict) -> dict:
    extractor_cls = EXTRACTOR_MAP.get(source["type"])
    if not extractor_cls:
        return {"source_id": source["id"], "error": "unsupported source type"}

    start = time.time()
    pushed = 0
    filtered = 0
    found = 0
    error_message = None

    try:
        extractor = extractor_cls(source)
        articles = await extractor.fetch()
        found = len(articles)

        for article in articles:
            if await dedup.is_duplicate(article.url, article.title):
                filtered += 1
                continue

            print("[scraper] scraped article:", article.to_dict())
            await queue.push(article.to_dict())
            await dedup.mark_seen(article.url, article.title)
            pushed += 1

    except Exception as exc:
        error_message = str(exc)

    duration_ms = int((time.time() - start) * 1000)
    result = {
        "source_id": source["id"],
        "found": found,
        "pushed": pushed,
        "filtered": filtered,
        "duration_ms": duration_ms,
    }
    if error_message:
        result["error"] = error_message

    return result

async def run_all_sources() -> list[dict]:
    tasks = [run_source(source) for source in SOURCES]
    return await asyncio.gather(*tasks)

async def scheduler_loop(check_interval: int = 30) -> None:
    last_run: dict[str, float] = {source["id"]: 0.0 for source in SOURCES}
    print("[scraper] scheduler started")

    while True:
        now = time.time()
        due = [source for source in SOURCES if now - last_run[source["id"]] >= source["interval_min"] * 60]

        if due:
            print(f"[scraper] running {len(due)} sources")
            results = await asyncio.gather(*[run_source(source) for source in due])
            for source in due:
                last_run[source["id"]] = now
            for result in results:
                print(f"[scraper] {result['source_id']} found={result['found']} pushed={result['pushed']} filtered={result['filtered']} time={result['duration_ms']}ms")

        await asyncio.sleep(check_interval)
