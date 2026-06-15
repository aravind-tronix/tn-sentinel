"""
Historical scraper using GDELT DOC 2.0 API.

Fetches Tamil Nadu crime/incident articles month-by-month (newest first)
and pushes them into the existing Redis queue for the pipeline to process.

Usage:
  ./venv/bin/python scripts/historical_gdelt.py
  ./venv/bin/python scripts/historical_gdelt.py --start 2021-01 --end 2026-05 --pause 1.5

Progress is saved to scripts/gdelt_progress.json — safe to stop and resume anytime.
"""

import asyncio
import argparse
import json
import sys
from calendar import monthrange
from datetime import datetime, date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from local_server.scraper.queue import RawArticleQueue
from local_server.scraper.extractors.base import RawArticle
from local_server.scraper.dedup import Deduplicator

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

QUERIES = [
    "Tamil Nadu crime",
    "Tamil Nadu murder OR killed OR stabbed",
    "Tamil Nadu police arrested",
    "Tamil Nadu drugs OR ganja OR narcotics",
    "Tamil Nadu robbery OR theft",
    "Tamil Nadu rape OR sexual assault",
    "Tamil Nadu road accident",
    "Tamil Nadu fraud OR cybercrime OR scam",
]

ARTICLE_SELECTORS = [
    "article",
    "div.post-content",
    "div.entry-content",
    "div.article-content",
    "div.article-text",
    "div.article__content",
    "div.storyline",
    "section.article-content",
    "div.content",
    "div#content",
    "div.story-content",
    "div.news-detail",
    "div.article-body",
    "div.detail-content",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)",
    "Accept-Language": "en,ta;q=0.9",
}

PROGRESS_FILE = Path(__file__).parent / "gdelt_progress.json"


def months_newest_first(start: str, end: str) -> list[tuple[str, str, str]]:
    """Return list of (gdelt_start, gdelt_end, label) tuples, newest first."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))

    result = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        last_day = monthrange(y, m)[1]
        result.append((
            f"{y}{m:02d}01000000",
            f"{y}{m:02d}{last_day}235959",
            f"{y}-{m:02d}",
        ))
        m += 1
        if m > 12:
            m = 1
            y += 1

    result.reverse()
    return result


def load_progress() -> set[str]:
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()


def save_progress(done: set[str]) -> None:
    PROGRESS_FILE.write_text(json.dumps(sorted(done), indent=2))


async def gdelt_fetch(query: str, start_dt: str, end_dt: str) -> list[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": 250,
        "startdatetime": start_dt,
        "enddatetime": end_dt,
        "format": "json",
    }
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(GDELT_URL, params=params)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"  ⏳ GDELT rate limited — waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json().get("articles", [])
        except Exception as e:
            print(f"  ⚠️  GDELT error (attempt {attempt+1}): {e}")
            await asyncio.sleep(6)
    return []


def extract_text(html: str) -> str | None:
    page = BeautifulSoup(html, "html.parser")

    for selector in ARTICLE_SELECTORS:
        node = page.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text and len(text) > 100:
                return text

    best = None
    for node in page.find_all(["article", "div", "section"]):
        if node.name in {"script", "style", "header", "footer", "nav", "aside"}:
            continue
        text = node.get_text(" ", strip=True)
        if not text or len(text) < 250:
            continue
        tag_count = len(node.find_all())
        if tag_count and len(text) / tag_count < 25:
            continue
        if best is None or len(text) > len(best):
            best = text
    return best


async def fetch_article_text(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return extract_text(resp.text)
    except Exception:
        return None


def parse_gdelt_date(seendate: str) -> datetime | None:
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
    except Exception:
        return None


async def main(start: str, end: str, pause: float) -> None:
    queue = RawArticleQueue()
    dedup = Deduplicator()
    seen_urls: set[str] = set()
    done = load_progress()

    months = months_newest_first(start, end)
    total_queued = 0
    total_skipped = 0

    print(f"📅 Range: {end} → {start}  ({len(months)} months × {len(QUERIES)} queries)")
    print(f"⏭️  Already completed: {len(done)} batches")
    print(f"⏱️  Pause between fetches: {pause}s")
    print()

    for start_dt, end_dt, label in months:
        for query in QUERIES:
            key = f"{label}|{query}"
            if key in done:
                continue

            print(f"📡 [{label}] {query}")
            articles = await gdelt_fetch(query, start_dt, end_dt)
            print(f"   GDELT returned {len(articles)} articles")

            batch_queued = 0
            for art in articles:
                url = art.get("url", "").strip()
                title = art.get("title", "").strip()

                if not url or url in seen_urls:
                    total_skipped += 1
                    continue

                if await dedup.is_duplicate(url, title):
                    seen_urls.add(url)
                    total_skipped += 1
                    continue

                seen_urls.add(url)

                text = await fetch_article_text(url)
                if not text:
                    total_skipped += 1
                    continue

                raw = RawArticle(
                    source_id="gdelt_historical",
                    source_name=f"GDELT/{art.get('domain', 'unknown')}",
                    url=url,
                    title=title,
                    text=text,
                    language="en",
                    published_at=parse_gdelt_date(art.get("seendate", "")),
                )
                await dedup.mark_seen(url, title)
                await queue.push(raw.to_dict())
                batch_queued += 1
                total_queued += 1
                await asyncio.sleep(pause)

            print(f"   ✅ queued {batch_queued} | total {total_queued} | skipped {total_skipped}")

            done.add(key)
            save_progress(done)

            await asyncio.sleep(6.0)  # GDELT enforces 1 req / 5s

    queue_len = await queue.length()
    print(f"\n🎉 Finished. Queued: {total_queued} | Skipped: {total_skipped} | Queue depth: {queue_len}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GDELT historical scraper for Tamil Nadu incidents")
    parser.add_argument("--start", default="2021-01", help="Start month YYYY-MM (default: 2021-01)")
    parser.add_argument("--end", default=date.today().strftime("%Y-%m"), help="End month YYYY-MM (default: current month)")
    parser.add_argument("--pause", type=float, default=1.5, help="Seconds between article fetches (default: 1.5)")
    args = parser.parse_args()

    asyncio.run(main(args.start, args.end, args.pause))
