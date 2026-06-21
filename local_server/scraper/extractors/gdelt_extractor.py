"""
GDELT DOC 2.0 live extractor.
Queries the last N minutes on each run — designed to be called by the scheduler
like any other source. Respects the 1 req/5s GDELT rate limit via per-call delays.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from .base import BaseExtractor, RawArticle

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

GDELT_QUERIES = [
    "Tamil Nadu crime",
    "Tamil Nadu murder OR killed OR stabbed",
    "Tamil Nadu police arrested",
    "Tamil Nadu drugs OR ganja OR narcotics",
    "Tamil Nadu robbery OR theft",
    "Tamil Nadu rape OR sexual assault",
    "Tamil Nadu road accident",
    "Tamil Nadu fraud OR cybercrime OR scam",
    "Tamil Nadu civic OR panchayat OR grievance",
    "Tamil Nadu infrastructure OR water supply OR power cut",
]

ARTICLE_SELECTORS = [
    "article", "div.post-content", "div.entry-content", "div.article-content",
    "div.article-text", "div.article__content", "div.storyline",
    "section.article-content", "div.content", "div.story-content",
    "div.news-detail", "div.article-body", "div.detail-content",
]


class GDELTExtractor(BaseExtractor):
    """Live GDELT extractor — fetches articles from the last N minutes."""

    async def _fetch_article_text(self, url: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)"}) as client:
                r = await client.get(url)
                r.raise_for_status()
        except Exception:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for sel in ARTICLE_SELECTORS:
            node = soup.select_one(sel)
            if node:
                for tag in node.find_all(["script", "style", "nav", "footer", "aside"]):
                    tag.decompose()
                text = node.get_text(" ", strip=True)
                if len(text) > 200:
                    return text
        return None

    async def _query_gdelt(self, query: str, since: datetime, until: datetime, client: httpx.AsyncClient) -> list[dict]:
        fmt = "%Y%m%d%H%M%S"
        params = {
            "query":         query,
            "mode":          "artlist",
            "maxrecords":    "50",
            "startdatetime": since.strftime(fmt),
            "enddatetime":   until.strftime(fmt),
            "format":        "json",
            "sort":          "DateDesc",
        }
        for attempt in range(3):
            try:
                r = await client.get(GDELT_URL, params=params, timeout=30)
                if r.status_code == 429:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                return data.get("articles") or []
            except Exception:
                await asyncio.sleep(6)
        return []

    async def fetch(self) -> list[RawArticle]:
        window_minutes = self.source.get("window_minutes", 60)
        until = datetime.now(timezone.utc).replace(tzinfo=None)
        since = until - timedelta(minutes=window_minutes)

        seen_urls: set[str] = set()
        articles: list[RawArticle] = []

        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)"}) as client:
            for query in GDELT_QUERIES:
                await asyncio.sleep(6)  # enforce GDELT 1 req/5s limit
                results = await self._query_gdelt(query, since, until, client)
                for item in results:
                    url = item.get("url", "").strip()
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)

                    title = self.normalize_text(item.get("title") or "")
                    if not title:
                        continue

                    pub_str = item.get("seendate", "")
                    published_at = None
                    if pub_str:
                        try:
                            published_at = datetime.strptime(pub_str[:14], "%Y%m%dT%H%M%S")
                        except Exception:
                            pass

                    full_text = await self._fetch_article_text(url)
                    text = self.normalize_text(full_text or title)

                    articles.append(RawArticle(
                        source_id=self.source["id"],
                        source_name=self.source["name"],
                        url=url,
                        title=title,
                        text=text,
                        summary=self.normalize_text(text[:400]) if text else None,
                        language=item.get("language", "en")[:2],
                        published_at=published_at,
                        image_url=None,
                    ))

        return articles
