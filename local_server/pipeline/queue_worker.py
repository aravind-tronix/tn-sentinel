import asyncio
import json
import logging
import time
from typing import Optional

from local_server.scraper.queue import RawArticleQueue
from local_server.pipeline.worker import process_and_save

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[queue_worker] %(message)s")

async def consume_batch(max_items: int = 5, pause_seconds: float = 0.5) -> int:
    queue = RawArticleQueue()
    processed = 0

    total_time_ms = 0
    for _ in range(max_items):
        raw_article = await queue.pop()
        if raw_article is None:
            break

        if isinstance(raw_article, str):
            try:
                raw_article = json.loads(raw_article)
            except json.JSONDecodeError:
                logger.error("Skipping invalid JSON payload")
                continue

        start = time.perf_counter()
        try:
            incident, created = await asyncio.wait_for(
                process_and_save(raw_article),
                timeout=540.0,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "Timeout processing article: %s time_ms=%s",
                raw_article.get("url") or raw_article.get("title") or "unknown",
                duration_ms,
            )
            continue
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "Error processing article %s: %s time_ms=%s",
                raw_article.get("url") or raw_article.get("title") or "unknown",
                exc,
                duration_ms,
            )
            continue

        duration_ms = int((time.perf_counter() - start) * 1000)
        total_time_ms += duration_ms
        if incident is None:
            logger.info(
                "Filtered raw article: %s time_ms=%s",
                raw_article.get("title"),
                duration_ms,
            )
        else:
            logger.info(
                "Processed incident %s created=%s time_ms=%s",
                incident.id,
                created,
                duration_ms,
            )
        processed += 1
        await asyncio.sleep(pause_seconds)

    if processed:
        avg_time_ms = int(total_time_ms / processed)
        logger.info(
            "Finished processing %s items total_time_ms=%s avg_time_ms=%s",
            processed,
            total_time_ms,
            avg_time_ms,
        )
    else:
        logger.info("Finished processing 0 items")

    return processed

async def run_daemon(pause_seconds: float = 0.5) -> None:
    """Run forever: block-wait for items, process each one as it arrives."""
    from local_server.pipeline.worker import init_worker
    await init_worker()

    queue = RawArticleQueue()
    logger.info("Queue worker daemon started — waiting for items (Ctrl-C to stop)")

    while True:
        raw_article = await queue.blpop(timeout=5.0)
        if raw_article is None:
            continue  # timeout, no item — loop and wait again

        if isinstance(raw_article, str):
            try:
                raw_article = json.loads(raw_article)
            except json.JSONDecodeError:
                logger.error("Skipping invalid JSON payload")
                continue

        start = time.perf_counter()
        try:
            incident, created = await asyncio.wait_for(
                process_and_save(raw_article),
                timeout=540.0,
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "Timeout processing article: %s time_ms=%s",
                raw_article.get("url") or raw_article.get("title") or "unknown",
                duration_ms,
            )
            continue
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "Error processing article %s: %s time_ms=%s",
                raw_article.get("url") or raw_article.get("title") or "unknown",
                exc,
                duration_ms,
            )
            continue

        duration_ms = int((time.perf_counter() - start) * 1000)
        if incident is None:
            logger.info("Filtered: %s time_ms=%s", raw_article.get("title"), duration_ms)
        else:
            logger.info("Processed incident %s created=%s time_ms=%s", incident.id, created, duration_ms)

        if pause_seconds > 0:
            await asyncio.sleep(pause_seconds)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Consume raw articles from Redis queue.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N items then exit (omit for daemon mode)")
    parser.add_argument("--pause", type=float, default=0.5, help="Seconds to pause between items")
    args = parser.parse_args()

    if args.limit is not None:
        logger.info("Starting queue worker (one-shot); limit=%s pause=%s", args.limit, args.pause)
        count = await consume_batch(max_items=args.limit, pause_seconds=args.pause)
        logger.info("Finished processing %s items", count)
    else:
        await run_daemon(pause_seconds=args.pause)

if __name__ == "__main__":
    asyncio.run(main())
