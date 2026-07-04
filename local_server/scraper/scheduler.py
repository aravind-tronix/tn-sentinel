import asyncio
import time
from datetime import datetime, timezone
from typing import Callable

from local_server.config import get_settings

_settings = get_settings()

from .registry import SOURCES
from .extractors.rss_extractor import RSSExtractor
from .extractors.html_extractor import HTMLExtractor
from .extractors.gdelt_extractor import GDELTExtractor
from .extractors.newsapi_extractor import NewsAPIExtractor
from .queue import RawArticleQueue
from .dedup import Deduplicator

EXTRACTOR_MAP = {
    "rss":     RSSExtractor,
    "html":    HTMLExtractor,
    "gdelt":   GDELTExtractor,
    "newsapi": NewsAPIExtractor,
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

        max_age_hours = _settings.scraper_max_age_hours
        for article in articles:
            # Skip articles older than max_age_hours
            if article.published_at:
                try:
                    pub = article.published_at
                    if isinstance(pub, str):
                        from dateparser import parse as dp_parse
                        pub = dp_parse(pub)
                    if pub:
                        pub = pub.replace(tzinfo=timezone.utc) if pub.tzinfo is None else pub.astimezone(timezone.utc)
                        age_h = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
                        if age_h > max_age_hours:
                            filtered += 1
                            continue
                except Exception:
                    pass

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
