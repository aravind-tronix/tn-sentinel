"""
Backfill TN Intel incidents for an exact date range using GDELT, then process
through the normal worker pipeline so Postgres + DynamoDB + website mirrors update.

Example:
  ./venv/bin/python scripts/backfill_date_range.py --start 2026-08-05 --end 2026-08-13 --max-per-day 8
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import urllib.parse
from datetime import datetime, timedelta, date, time
from pathlib import Path

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from local_server.db.models import AsyncSessionLocal, Incident, init_db
from local_server.pipeline.worker import process_and_save
from local_server.scraper.extractors.base import RawArticle
from local_server.config import get_settings

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
QUERIES = [
    "Tamil Nadu crime",
    "Tamil Nadu murder OR killed OR stabbed",
    "Tamil Nadu police arrested",
    "Tamil Nadu road accident",
    "Tamil Nadu ganja OR drugs OR narcotics",
    "Tamil Nadu cybercrime OR fraud OR scam",
]
LOCATION_TERMS = [
    "tamil nadu", " tn ", "chennai", "tiruppur", "tirupur", "coimbatore", "madurai",
    "tiruchi", "tiruchy", "trichy", "salem", "tirunelveli", "vellore", "erode",
    "thoothukudi", "dindigul", "kanchipuram", "krishnagiri", "namakkal", "theni",
    "karur", "dharmapuri", "nilgiris", "ariyalur", "perambalur", "cuddalore",
    "villupuram", "nagapattinam", "thanjavur", "tiruvarur", "pudukkottai",
    "sivaganga", "virudhunagar", "ramanathapuram", "tenkasi", "kanyakumari",
    "ranipet", "chengalpattu", "tirupattur", "kallakurichi", "mayiladuthurai",
    "rameswaram", "hosur", "ooty", "kovai", "tiruvallur", "tiruvottiyur",
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
    "div.story-content",
    "div.news-detail",
    "div.article-body",
    "div.detail-content",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)",
    "Accept-Language": "en,ta;q=0.9",
}


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def gdelt_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def parse_gdelt_date(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, fmt)
        except Exception:
            pass
    try:
        from dateparser import parse

        parsed = parse(value)
        return parsed.replace(tzinfo=None) if parsed else None
    except Exception:
        return None


def extract_text(html: str) -> str | None:
    page = BeautifulSoup(html, "html.parser")
    for tag in page.find_all(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    for selector in ARTICLE_SELECTORS:
        node = page.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text and len(text) > 180:
                return text
    best = None
    for node in page.find_all(["article", "div", "section"]):
        text = node.get_text(" ", strip=True)
        if len(text) > 250 and (best is None or len(text) > len(best)):
            best = text
    return best


def clean_html_text(value: str | None) -> str:
    if not value:
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def has_location_signal(title: str, url: str = "", source_name: str = "") -> bool:
    haystack = f" {title} {url} {source_name} ".lower().replace("-", " ")
    return any(term in haystack for term in LOCATION_TERMS)


async def existing_urls() -> set[str]:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(Incident.url))
        return {row[0] for row in rows.all() if row[0]}


async def gdelt_fetch(client: httpx.AsyncClient, query: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": 50,
        "startdatetime": gdelt_ts(start_dt),
        "enddatetime": gdelt_ts(end_dt),
        "format": "json",
        "sort": "DateDesc",
    }
    for attempt in range(3):
        try:
            resp = await client.get(GDELT_URL, params=params, timeout=30)
            if resp.status_code == 429:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json().get("articles", []) or []
        except Exception as exc:
            print(f"  gdelt error attempt={attempt+1} query={query!r}: {exc}", flush=True)
            await asyncio.sleep(5 * (attempt + 1))
    return []


async def newsapi_fetch(client: httpx.AsyncClient, query: str, start_dt: datetime, end_dt: datetime) -> list[dict]:
    settings = get_settings()
    if not settings.newsapi_key:
        return []
    params = {
        "q": query,
        "from": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 30,
        "apiKey": settings.newsapi_key,
    }
    try:
        resp = await client.get(NEWSAPI_URL, params=params, timeout=30)
        resp.raise_for_status()
        out = []
        for item in resp.json().get("articles") or []:
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title or url == "https://removed.com":
                continue
            out.append(
                {
                    "provider": "newsapi",
                    "url": url,
                    "title": title,
                    "seendate": item.get("publishedAt") or "",
                    "summary": item.get("description") or item.get("content") or "",
                    "domain": (item.get("source") or {}).get("name") or "newsapi",
                    "source_name": (item.get("source") or {}).get("name") or "NewsAPI",
                }
            )
        return out
    except Exception as exc:
        print(f"  newsapi error query={query!r}: {exc}", flush=True)
        return []


async def google_news_fetch(client: httpx.AsyncClient, query: str, start_day: date, end_day_exclusive: date) -> list[dict]:
    dated_query = f"{query} after:{start_day.isoformat()} before:{end_day_exclusive.isoformat()}"
    params = {
        "q": dated_query,
        "hl": "en-IN",
        "gl": "IN",
        "ceid": "IN:en",
    }
    try:
        resp = await client.get(GOOGLE_NEWS_RSS_URL, params=params, timeout=25)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        out = []
        for item in feed.entries:
            url = getattr(item, "link", "") or ""
            title = getattr(item, "title", "") or ""
            published = getattr(item, "published", "") or ""
            source_name = "Google News"
            source = getattr(item, "source", None)
            if isinstance(source, dict):
                source_name = source.get("title") or source_name
            if not url or not title:
                continue
            out.append(
                {
                    "provider": "google_news_rss",
                    "url": url,
                    "title": title,
                    "seendate": published,
                    "summary": getattr(item, "summary", "") or "",
                    "domain": urllib.parse.urlparse(url).netloc or "google-news",
                    "source_name": source_name,
                }
            )
        return out
    except Exception as exc:
        print(f"  google rss error query={query!r}: {exc}", flush=True)
        return []


async def fetch_article_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=25, follow_redirects=True)
        resp.raise_for_status()
        return extract_text(resp.text)
    except Exception:
        return None


async def main(start: date, end: date, max_per_day: int, max_attempts_per_day: int, include_gdelt: bool, skip_newsapi: bool, dry_run: bool) -> None:
    await init_db()
    known_urls = await existing_urls()
    seen_urls = set(known_urls)
    total_candidates = 0
    total_processed = 0
    total_created = 0
    total_filtered = 0
    total_skipped = 0

    print(f"Backfill {start} → {end}; max_per_day={max_per_day}; max_attempts_per_day={max_attempts_per_day}; include_gdelt={include_gdelt}; skip_newsapi={skip_newsapi}; dry_run={dry_run}", flush=True)
    print(f"Existing DB URLs: {len(known_urls)}", flush=True)

    async with httpx.AsyncClient(headers=HEADERS) as client:
        for day in daterange(start, end):
            day_start = datetime.combine(day, time.min)
            day_end = datetime.combine(day, time.max).replace(microsecond=0)
            day_candidates: list[dict] = []
            day_urls: set[str] = set()

            print(f"\n=== {day.isoformat()} ===", flush=True)
            next_day = day + timedelta(days=1)
            if not skip_newsapi:
                for query in QUERIES:
                    articles = await newsapi_fetch(client, query, day_start, day_end)
                    print(f"  NewsAPI {query}: {len(articles)}", flush=True)
                    for art in articles:
                        url = (art.get("url") or "").strip()
                        title = (art.get("title") or "").strip()
                        source_name = art.get("source_name") or ""
                        if not url or url in seen_urls or url in day_urls or not title:
                            continue
                        if not has_location_signal(title, url, source_name):
                            continue
                        day_urls.add(url)
                        day_candidates.append(art)

            for query in QUERIES:
                articles = await google_news_fetch(client, query, day, next_day)
                print(f"  GoogleRSS {query}: {len(articles)}", flush=True)
                for art in articles:
                    url = (art.get("url") or "").strip()
                    title = (art.get("title") or "").strip()
                    source_name = art.get("source_name") or ""
                    if not url or url in seen_urls or url in day_urls or not title:
                        continue
                    if not has_location_signal(title, url, source_name):
                        continue
                    day_urls.add(url)
                    day_candidates.append(art)

            if include_gdelt:
                for query in QUERIES:
                    await asyncio.sleep(6)  # GDELT rate limit: ~1 request / 5s
                    articles = await gdelt_fetch(client, query, day_start, day_end)
                    print(f"  GDELT {query}: {len(articles)}", flush=True)
                    for art in articles:
                        url = (art.get("url") or "").strip()
                        title = (art.get("title") or "").strip()
                        source_name = art.get("source_name") or ""
                        if not url or url in seen_urls or url in day_urls or not title:
                            continue
                        if not has_location_signal(title, url, source_name):
                            continue
                        pub = parse_gdelt_date(art.get("seendate", ""))
                        if pub and not (day_start <= pub <= day_end):
                            continue
                        day_urls.add(url)
                        day_candidates.append(art)

            # Prefer likely crime before generic/noisy sources.
            def score(art: dict) -> int:
                text = f"{art.get('title','')} {art.get('url','')}".lower()
                terms = ["murder", "killed", "stab", "arrest", "police", "rape", "assault", "ganja", "drug", "theft", "robbery", "fraud", "accident"]
                return sum(1 for t in terms if t in text)

            day_candidates.sort(key=score, reverse=True)
            total_candidates += len(day_candidates)
            print(f"  unique new candidates: {len(day_candidates)}", flush=True)

            accepted_today = 0
            attempted_today = 0
            for art in day_candidates:
                if accepted_today >= max_per_day:
                    break
                if attempted_today >= max_attempts_per_day:
                    break
                url = art.get("url", "").strip()
                title = art.get("title", "").strip()
                provider = art.get("provider") or "gdelt_backfill"
                provider_text = " ".join([title, clean_html_text(art.get("summary"))]).strip()
                if provider == "google_news_rss":
                    text = provider_text
                elif provider == "newsapi" and len(provider_text) >= 80:
                    text = provider_text
                else:
                    text = await fetch_article_text(client, url)
                    if not text:
                        text = provider_text
                if len(text) < 40:
                    total_skipped += 1
                    continue
                pub = parse_gdelt_date(art.get("seendate", ""))
                raw = RawArticle(
                    source_id=provider,
                    source_name=art.get("source_name") or f"GDELT/{art.get('domain', 'unknown')}",
                    url=url,
                    title=title,
                    text=text,
                    summary=text[:500],
                    language="en",
                    published_at=pub,
                    image_url=None,
                ).to_dict()

                print(f"  process: {title[:90]}", flush=True)
                attempted_today += 1
                if dry_run:
                    total_processed += 1
                    accepted_today += 1
                    continue

                try:
                    incident, created = await asyncio.wait_for(process_and_save(raw), timeout=240)
                except Exception as exc:
                    print(f"    ERROR {type(exc).__name__}: {exc}", flush=True)
                    total_skipped += 1
                    continue

                total_processed += 1
                if incident is None:
                    total_filtered += 1
                    print("    filtered", flush=True)
                else:
                    total_created += int(created)
                    accepted_today += 1
                    seen_urls.add(url)
                    print(f"    saved id={incident.id} created={created} district={incident.district} category={incident.category}", flush=True)

            print(f"  day saved/accepted={accepted_today} attempted={attempted_today}", flush=True)

    print(
        f"\nDONE candidates={total_candidates} processed={total_processed} created={total_created} filtered={total_filtered} skipped={total_skipped}",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--max-per-day", type=int, default=8)
    parser.add_argument("--max-attempts-per-day", type=int, default=10)
    parser.add_argument("--include-gdelt", action="store_true")
    parser.add_argument("--skip-newsapi", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        main(
            datetime.strptime(args.start, "%Y-%m-%d").date(),
            datetime.strptime(args.end, "%Y-%m-%d").date(),
            args.max_per_day,
            args.max_attempts_per_day,
            args.include_gdelt,
            args.skip_newsapi,
            args.dry_run,
        )
    )
