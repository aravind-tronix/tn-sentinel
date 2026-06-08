import asyncio
import logging
import time
from typing import Optional

import httpx
from sqlalchemy import select

from local_server.config import get_settings
from local_server.db.models import AsyncSessionLocal, Incident, init_db
from local_server.pipeline.llm_chains import process_article

settings = get_settings()
logger = logging.getLogger(__name__)

async def save_incident(incident_data: dict) -> tuple[Incident, bool]:
    """Save a single processed incident to PostgreSQL if it is not already present."""
    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(Incident).where(Incident.url == incident_data["url"]))
        existing_incident = existing.scalars().first()
        if existing_incident:
            return existing_incident, False

        incident = Incident(
            url=incident_data["url"],
            title=incident_data["title"],
            summary=incident_data.get("summary"),
            raw_text=incident_data.get("raw_text"),
            source_id=incident_data.get("source_id"),
            source_name=incident_data.get("source_name"),
            district=incident_data.get("district"),
            category=incident_data.get("category"),
            viral_score=incident_data.get("viral_score", 0),
            sentiment=incident_data.get("sentiment"),
            language=incident_data.get("language", "en"),
            confidence=incident_data.get("confidence", 0.0),
            entities=incident_data.get("entities", {}),
            image_url=incident_data.get("image_url"),
            published_at=incident_data.get("published_at"),
            scraped_at=incident_data.get("scraped_at"),
            processed_at=incident_data.get("processed_at"),
            embedding=incident_data.get("embedding"),
        )
        session.add(incident)
        await session.commit()
        await session.refresh(incident)
        return incident, True


async def publish_event(event: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.event_broadcaster_url}/broadcast",
                json=event,
                headers={"x-api-key": settings.llm_api_secret},
            )
    except Exception:
        return


async def process_and_save(raw_article: dict) -> tuple[Optional[Incident], bool]:
    """Run the LLM pipeline and persist the resulting incident."""
    process_start = time.perf_counter()
    incident_data = await process_article(raw_article)
    process_duration_ms = int((time.perf_counter() - process_start) * 1000)

    if not incident_data:
        logger.info(
            "Filtered raw article: %s llm_ms=%s",
            raw_article.get("url") or raw_article.get("title") or "unknown",
            process_duration_ms,
        )
        return None, False

    save_start = time.perf_counter()
    incident, created = await save_incident(incident_data)
    save_duration_ms = int((time.perf_counter() - save_start) * 1000)
    total_duration_ms = process_duration_ms + save_duration_ms

    logger.info(
        "Saved incident: %s created=%s llm_ms=%s db_ms=%s total_ms=%s",
        incident_data.get("url") or incident.id,
        created,
        process_duration_ms,
        save_duration_ms,
        total_duration_ms,
    )

    if created:
        await publish_event({"type": "incident", "incident": incident_data})
    return incident, created

async def process_batch(raw_articles: list[dict]) -> list[Incident]:
    """Process a batch of raw articles serially to avoid VRAM pressure."""
    results = []
    for raw_article in raw_articles:
        incident = await process_and_save(raw_article)
        if incident:
            results.append(incident)
        await asyncio.sleep(0.5)
    return results

async def init_worker() -> None:
    await init_db()

if __name__ == "__main__":
    import json
    import sys

    async def main():
        await init_worker()
        if len(sys.argv) > 1:
            sample_path = sys.argv[1]
            with open(sample_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            processed = await process_and_save(payload)
            print("Processed:", processed.id if processed else None)
        else:
            print("Usage: python worker.py sample_article.json")

    asyncio.run(main())
