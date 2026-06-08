import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .base import BaseExtractor, RawArticle

class HTMLExtractor(BaseExtractor):
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
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
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

    async def fetch(self) -> list[RawArticle]:
        selectors = self.source.get("selectors", {})
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TNIntelBot/1.0)",
                "Accept-Language": "ta,en;q=0.9",
            },
        ) as client:
            response = await client.get(self.source["url"])
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        article_nodes = soup.select(selectors.get("articles", "article, div.post, li"))
        if not article_nodes:
            for fallback_selector in [
                "article",
                "div.article",
                "div[class*=ArtCont]",
                "div[class*=ArticleBodyCont]",
                "div[class*=story]",
                "div[class*=news]",
                "div[class*=post]",
                "div[class*=card]",
            ]:
                article_nodes = soup.select(fallback_selector)
                if article_nodes:
                    break

        articles: list[RawArticle] = []

        for node in article_nodes[:30]:
            title_el = node.select_one(selectors.get("title", "a"))
            link_el = node.select_one(selectors.get("link", "a[href]"))
            summary_el = node.select_one(selectors.get("summary", "p"))
            time_el = node.select_one(selectors.get("time", "time, span.date"))
            image_el = node.select_one(selectors.get("image", "img[src]"))

            title = title_el.get_text(strip=True) if title_el else ""
            href = link_el.get("href", "") if link_el else ""
            url = urljoin(self.source["url"], href)
            summary = summary_el.get_text(" ", strip=True) if summary_el else ""
            time_text = time_el.get("datetime") if time_el and time_el.has_attr("datetime") else (time_el.get_text(strip=True) if time_el else "")
            image_url = urljoin(self.source["url"], image_el.get("src", "")) if image_el else None

            if not title or not url:
                continue

            full_text = await self._fetch_full_text(url)
            text = self.normalize_text(full_text or summary or title)
            summary_text = self.normalize_text(summary)[:500] if summary else (self.normalize_text(full_text)[:500] if full_text else None)

            article = RawArticle(
                source_id=self.source["id"],
                source_name=self.source["name"],
                url=url,
                title=self.normalize_text(title),
                text=text,
                summary=summary_text,
                language=self.source.get("language", "en"),
                published_at=self.parse_date(time_text),
                image_url=image_url,
            )
            articles.append(article)

        return articles
