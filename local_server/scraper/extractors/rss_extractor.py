import asyncio
import base64
import re
import feedparser
import httpx
from bs4 import BeautifulSoup
from .base import BaseExtractor, RawArticle

class RSSExtractor(BaseExtractor):
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
        "div.text-story-m_story-content-inner-wrapper__s3KPp",
        "div.text-story-m_gap-16__5BPKQ",
        "div.arr--story-page-card-wrapper",
        "div.arr--text-element",
        "div.story-m__wrapper__iut-B",
    ]

    async def _fetch_full_text(self, url: str) -> str | None:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)",
                "Accept-Language": "ta,en;q=0.9",
            }
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
                response = await client.get(url)
                response.raise_for_status()
        except Exception:
            return None

        page = BeautifulSoup(response.text, "html.parser")
        return self._extract_text_from_page(page)

    def _extract_text_from_page(self, page: BeautifulSoup) -> str | None:
        selectors = self.source.get("selectors", {})
        content_selector = selectors.get("content")

        if content_selector:
            node = page.select_one(content_selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text

        for selector in self.ARTICLE_SELECTORS:
            node = page.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text

        return self._extract_longest_text(page)

    def _extract_longest_text(self, page: BeautifulSoup) -> str | None:
        best_text = None
        for node in page.find_all(["article", "div", "section"]):
            if node.name in {"script", "style", "header", "footer", "nav", "aside"}:
                continue
            text = node.get_text(" ", strip=True)
            if not text or len(text) < 250:
                continue
            tag_count = len(node.find_all())
            if tag_count and len(text) / tag_count < 25:
                continue
            if best_text is None or len(text) > len(best_text):
                best_text = text
        return best_text

    def _decode_google_news_url(self, gn_url: str) -> str | None:
        if "news.google.com" not in gn_url:
            return None

        match = re.search(r"/articles/([^?]+)", gn_url)
        if not match:
            return None

        encoded = match.group(1)
        padded = encoded + "=" * (-len(encoded) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded)
        except Exception:
            return None

        url_match = re.search(rb"https?://[^\x00-\x1f\s\"<>]+", decoded)
        if url_match:
            try:
                return url_match.group(0).decode("utf-8", errors="ignore")
            except Exception:
                return None
        return None

    async def _resolve_redirect(self, url: str) -> str:
        if "news.google.com" not in url:
            return url

        decoded_url = self._decode_google_news_url(url)
        if decoded_url:
            return decoded_url

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(url)
                final_url = str(response.url)
                if final_url and final_url != url and "news.google.com" not in final_url:
                    return final_url

                page = BeautifulSoup(response.text, "html.parser")
                canonical = page.select_one('link[rel="canonical"]')
                if canonical and canonical.get("href") and "news.google.com" not in canonical.get("href"):
                    return canonical.get("href")
                og_url = page.select_one('meta[property="og:url"]')
                if og_url and og_url.get("content") and "news.google.com" not in og_url.get("content"):
                    return og_url.get("content")
        except Exception:
            pass
        return url

    def _resolve_entry_url(self, entry, url: str) -> str:
        source = entry.get("source")
        if isinstance(source, dict):
            source_url = source.get("href") or source.get("url")
            if source_url:
                return source_url
        return url

    def _is_polluted_text(self, text: str) -> bool:
        if not text:
            return False
        signals = [
            "Trending on The Hindu",
            "Stock Market Live Updates",
            "Frontline Current Issue",
            "Gold Rate Today",
        ]
        return sum(1 for signal in signals if signal in text) >= 2

    async def fetch(self) -> list[RawArticle]:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)",
            "Accept-Language": "ta,en;q=0.9",
        }

        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            response = await client.get(self.source["url"])
            response.raise_for_status()

        feed = feedparser.parse(response.text)
        articles: list[RawArticle] = []

        entries = []
        for entry in feed.entries[:25]:
            raw_summary = ""
            if entry.get("content"):
                raw_summary = " ".join(
                    content_item.get("value", "") for content_item in entry.get("content", []) if content_item.get("value")
                )
            if not raw_summary:
                raw_summary = entry.get("summary", "") or entry.get("description", "")
            clean_summary = BeautifulSoup(raw_summary, "html.parser").get_text(separator=" ", strip=True)
            url = entry.get("link", "") or entry.get("id", "")
            entries.append((entry, url, clean_summary))

        sem = asyncio.Semaphore(5)

        async def fetch_one(entry, url, clean_summary):
            resolved_url = self._resolve_entry_url(entry, url)
            full_url = await self._resolve_redirect(resolved_url)
            full_text = await self._fetch_full_text(full_url) if full_url else None
            if self._is_polluted_text(full_text):
                full_text = None
            text = self.normalize_text(full_text or clean_summary or (entry.get("title", "") or "").strip())
            summary = self.normalize_text(clean_summary)[:500] if clean_summary else (self.normalize_text(full_text)[:500] if full_text else None)
            return entry, full_url, text, summary

        async def fetch_limited(entry, url, clean_summary):
            async with sem:
                return await fetch_one(entry, url, clean_summary)

        results = await asyncio.gather(*[fetch_limited(entry, url, clean_summary) for entry, url, clean_summary in entries])

        for entry, url, text, summary in results:
            article = RawArticle(
                source_id=self.source["id"],
                source_name=self.source["name"],
                url=url,
                title=(entry.get("title", "") or "").strip(),
                text=text,
                summary=summary,
                language=self.source.get("language", "en"),
                published_at=self.parse_date(entry.get("published", "") or entry.get("updated", "")),
                tags=[tag.term for tag in entry.get("tags", [])] if entry.get("tags") else [],
            )

            if article.title and article.url:
                articles.append(article)

        return articles
