"""
NewsAPI.org extractor for Tamil Nadu news.
Requires NEWSAPI_KEY in config/.env (get free key at newsapi.org).
Skipped silently if key is not set.
"""
import httpx
from datetime import datetime, timedelta, timezone

from .base import BaseExtractor, RawArticle
from local_server.config import get_settings

NEWSAPI_URL = "https://newsapi.org/v2/everything"

QUERIES = [
    "Tamil Nadu crime",
    "Tamil Nadu police",
    "Tamil Nadu accident",
    "Tamil Nadu civic issues",
    "Tamil Nadu infrastructure",
]


class NewsAPIExtractor(BaseExtractor):
    async def fetch(self) -> list[RawArticle]:
        settings = get_settings()
        if not settings.newsapi_key:
            return []

        window_hours = self.source.get("window_hours", 24)
        since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        seen: set[str] = set()
        articles: list[RawArticle] = []

        async with httpx.AsyncClient(timeout=20) as client:
            for query in QUERIES:
                try:
                    r = await client.get(NEWSAPI_URL, params={
                        "q":        query,
                        "from":     since,
                        "language": "en",
                        "sortBy":   "publishedAt",
                        "pageSize": 20,
                        "apiKey":   settings.newsapi_key,
                    })
                    r.raise_for_status()
                    data = r.json()
                except Exception:
                    continue

                for item in data.get("articles") or []:
                    url = (item.get("url") or "").strip()
                    if not url or url in seen or url == "https://removed.com":
                        continue
                    seen.add(url)

                    title   = self.normalize_text(item.get("title") or "")
                    content = self.normalize_text(
                        (item.get("content") or "") + " " + (item.get("description") or "")
                    )
                    pub_str = item.get("publishedAt", "")
                    published_at = None
                    if pub_str:
                        try:
                            published_at = datetime.strptime(pub_str, "%Y-%m-%dT%H:%M:%SZ")
                        except Exception:
                            pass

                    articles.append(RawArticle(
                        source_id=self.source["id"],
                        source_name=item.get("source", {}).get("name") or self.source["name"],
                        url=url,
                        title=title,
                        text=content or title,
                        summary=self.normalize_text(item.get("description") or "")[:500] or None,
                        language="en",
                        published_at=published_at,
                        image_url=item.get("urlToImage"),
                    ))

        return articles
