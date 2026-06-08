from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import dateparser

@dataclass
class RawArticle:
    source_id: str
    source_name: str
    url: str
    title: str
    text: str
    summary: Optional[str] = None
    language: str = "en"
    published_at: Optional[datetime] = None
    scraped_at: datetime = field(default_factory=datetime.utcnow)
    image_url: Optional[str] = None
    author: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "summary": self.summary,
            "language": self.language,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "scraped_at": self.scraped_at.isoformat(),
            "image_url": self.image_url,
            "author": self.author,
            "tags": self.tags,
        }

class BaseExtractor(ABC):
    def __init__(self, source: dict):
        self.source = source

    @abstractmethod
    async def fetch(self) -> list[RawArticle]:
        pass

    def parse_date(self, value: str | None) -> Optional[datetime]:
        if not value:
            return None
        parsed = dateparser.parse(value, settings={"RETURN_AS_TIMEZONE_AWARE": False})
        return parsed

    def normalize_text(self, text: str) -> str:
        return text.strip().replace("\n", " ").replace("  ", " ")
