import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import redis.asyncio as redis
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine

from local_server.config import get_settings

settings = get_settings()

async def clear_redis_queue() -> int:
    r = redis.from_url(settings.redis_url)
    queue_name = settings.redis_raw_queue
    length = await r.llen(queue_name)
    if length > 0:
        await r.delete(queue_name)
    await r.close()
    return int(length)

async def clear_dedup_keys() -> int:
    r = redis.from_url(settings.redis_url)
    deleted = 0
    async for key in r.scan_iter('dedup:url:*'):
        await r.delete(key)
        deleted += 1
    async for key in r.scan_iter('dedup:title:*'):
        await r.delete(key)
        deleted += 1
    await r.close()
    return deleted

async def clear_database() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(
            sqlalchemy.text('TRUNCATE TABLE incidents, scrape_log RESTART IDENTITY CASCADE')
        )
    await engine.dispose()

async def main(args: argparse.Namespace) -> int:
    if not (args.queue or args.dedup or args.db or args.all):
        print('No target specified. Use --queue, --dedup, --db, or --all.')
        return 1

    if args.all or args.queue:
        print(f'Clearing Redis queue: {settings.redis_raw_queue}')
        cleared = await clear_redis_queue()
        print(f'  removed {cleared} queued article(s)')

    if args.all or args.dedup:
        print('Clearing Redis dedup keys: dedup:url:* and dedup:title:*')
        deleted = await clear_dedup_keys()
        print(f'  removed {deleted} dedup key(s)')

    if args.all or args.db:
        print('Clearing database tables: incidents, scrape_log')
        await clear_database()
        print('  database tables truncated successfully')

    return 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Clear scraper state: Redis queue, Redis dedup keys, and DB tables.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--queue', action='store_true', help='Clear the Redis raw article queue')
    parser.add_argument('--dedup', action='store_true', help='Clear Redis deduplication keys')
    parser.add_argument('--db', action='store_true', help='Clear the database tables')
    parser.add_argument('--all', action='store_true', help='Clear queue, dedup keys, and database')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args)))
