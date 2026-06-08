"""
Migrate all incidents from PostgreSQL to DynamoDB.

Usage:
    ./venv/bin/python scripts/migrate_pg_to_dynamo.py
    ./venv/bin/python scripts/migrate_pg_to_dynamo.py --dry-run   # count only
"""
import argparse
import asyncio
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boto3
from sqlalchemy import select

from local_server.config import get_settings
from local_server.db.models import AsyncSessionLocal, Incident

settings = get_settings()


def _clean(val):
    if val is None:
        return None
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, dict):
        return {k: _clean(v) for k, v in val.items() if v is not None}
    if isinstance(val, list):
        return [_clean(v) for v in val if v is not None]
    return val


def incident_to_dynamo_item(inc: Incident) -> dict:
    pub = inc.published_at
    scraped = inc.scraped_at
    pub_str = (pub or scraped or datetime.utcnow()).isoformat()

    district = (inc.district or "unknown").lower()
    category = inc.category or "Unknown"

    item = {
        "district":        district,
        "published_at_id": f"{pub_str}#{inc.id}",
        "cat_time":        f"{category}#{pub_str}",
        "category":        category,
        "source_id":       inc.source_id or "unknown",
        "published_at":    pub_str,
        "url":             inc.url,
        "id":              str(inc.id),
        "title":           inc.title,
        "summary":         inc.summary,
        "source_name":     inc.source_name,
        "viral_score":     inc.viral_score or 0,
        "sentiment":       inc.sentiment,
        "language":        inc.language or "en",
        "confidence":      Decimal(str(inc.confidence or 0.0)),
        "entities":        _clean(inc.entities) if inc.entities else {},
        "image_url":       inc.image_url,
        "scraped_at":      scraped.isoformat() if scraped else None,
    }

    # DynamoDB rejects None values
    return {k: v for k, v in item.items() if v is not None}


async def load_all_incidents() -> list[Incident]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Incident).order_by(Incident.id))
        return list(result.scalars().all())


def batch_write(table, items: list[dict]) -> int:
    written = 0
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
            written += 1
    return written


def main():
    parser = argparse.ArgumentParser(description="Migrate PostgreSQL incidents → DynamoDB")
    parser.add_argument("--dry-run", action="store_true", help="Count records only, no writes")
    args = parser.parse_args()

    if not settings.dynamodb_incidents_table:
        print("ERROR: DYNAMODB_INCIDENTS_TABLE is not set in config/.env")
        sys.exit(1)

    print(f"Source DB   : {settings.database_url.split('@')[-1]}")
    print(f"Target table: {settings.dynamodb_incidents_table} ({settings.aws_region})")

    print("\nLoading incidents from PostgreSQL…")
    incidents = asyncio.run(load_all_incidents())
    print(f"Found {len(incidents)} incidents")

    if args.dry_run or not incidents:
        print("Dry run — no writes performed.")
        return

    dynamo = boto3.resource("dynamodb", region_name=settings.aws_region)
    table = dynamo.Table(settings.dynamodb_incidents_table)

    print(f"\nMigrating to DynamoDB in batches of 25…")
    items = [incident_to_dynamo_item(inc) for inc in incidents]

    # Split into batches of 25 (DynamoDB batch_writer limit per flush)
    BATCH = 25
    total_written = 0
    for i in range(0, len(items), BATCH):
        chunk = items[i:i + BATCH]
        written = batch_write(table, chunk)
        total_written += written
        print(f"  [{total_written}/{len(items)}] written")

    print(f"\nMigration complete — {total_written} items written to DynamoDB.")


if __name__ == "__main__":
    main()
