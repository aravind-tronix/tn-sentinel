#!/usr/bin/env python3
"""
Backup and restore the Redis raw_articles queue to/from a JSON file.

Usage:
    ./venv/bin/python scripts/queue_backup.py backup            # save queue to queue_backup.json
    ./venv/bin/python scripts/queue_backup.py restore           # push queue_backup.json back to Redis
    ./venv/bin/python scripts/queue_backup.py status            # show current queue length
"""
import asyncio
import json
import sys
from pathlib import Path

import redis.asyncio as redis

QUEUE_KEY   = "raw_articles"
BACKUP_FILE = Path("scripts/queue_backup.json")
REDIS_URL   = "redis://localhost:6379"


async def backup():
    r = redis.from_url(REDIS_URL)
    items = await r.lrange(QUEUE_KEY, 0, -1)
    await r.aclose()
    data = [item.decode() if isinstance(item, bytes) else item for item in items]
    BACKUP_FILE.write_text(json.dumps(data, indent=2))
    print(f"Backed up {len(data)} items → {BACKUP_FILE}")


async def restore():
    if not BACKUP_FILE.exists():
        print(f"No backup file found at {BACKUP_FILE}")
        return
    data = json.loads(BACKUP_FILE.read_text())
    if not data:
        print("Backup file is empty.")
        return
    r = redis.from_url(REDIS_URL)
    current = await r.llen(QUEUE_KEY)
    # Push to the right (end) so backed-up items continue after any new live items
    await r.rpush(QUEUE_KEY, *data)
    total = await r.llen(QUEUE_KEY)
    await r.aclose()
    print(f"Restored {len(data)} items. Queue: {current} → {total}")


async def status():
    r = redis.from_url(REDIS_URL)
    length = await r.llen(QUEUE_KEY)
    await r.aclose()
    print(f"Queue length: {length}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "backup":
        asyncio.run(backup())
    elif cmd == "restore":
        asyncio.run(restore())
    elif cmd == "status":
        asyncio.run(status())
    else:
        print(__doc__)
