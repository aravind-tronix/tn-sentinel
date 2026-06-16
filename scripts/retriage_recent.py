"""
Re-run the (now stricter) triage prompt against recently saved incidents to flag
likely misclassified ones (court/political/accident stories that slipped through
the old looser triage prompt).

Does NOT delete anything by default — just prints a report. Pass --delete to
remove flagged incidents from Postgres (DynamoDB/UI mirror is left untouched;
re-sync separately if needed).

Usage:
  ./venv/bin/python scripts/retriage_recent.py --hours 48
  ./venv/bin/python scripts/retriage_recent.py --hours 48 --delete
"""

import asyncio
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from local_server.db.models import AsyncSessionLocal, Incident, init_db
from local_server.pipeline.llm_chains import triage_chain


async def main(hours: int, delete: bool) -> None:
    await init_db()
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Incident).where(Incident.processed_at >= cutoff).order_by(Incident.processed_at.desc())
        )
        incidents = result.scalars().all()

    print(f"🔎 Re-triaging {len(incidents)} incidents processed in the last {hours}h\n")

    flagged = []
    for inc in incidents:
        text = (inc.raw_text or inc.summary or inc.title or "").strip()
        if not text:
            continue
        try:
            result = await asyncio.wait_for(
                triage_chain.ainvoke({"text": text[:800]}),
                timeout=120.0,
            )
        except Exception as e:
            print(f"  ⚠️  triage error for id={inc.id}: {e}")
            continue

        verdict = str(result).strip().upper()
        is_yes = "YES" in verdict and "NO" not in verdict
        if not is_yes:
            flagged.append(inc)
            print(f"  ❌ id={inc.id} [{inc.category}] {inc.title}")

    print(f"\n📋 Flagged {len(flagged)} / {len(incidents)} as likely misclassified.")

    if delete and flagged:
        async with AsyncSessionLocal() as session:
            for inc in flagged:
                obj = await session.get(Incident, inc.id)
                if obj:
                    await session.delete(obj)
            await session.commit()
        print(f"🗑️  Deleted {len(flagged)} incidents from Postgres.")
    elif flagged:
        print("ℹ️  Re-run with --delete to remove these from Postgres.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-triage recently saved incidents")
    parser.add_argument("--hours", type=int, default=48, help="Look back window in hours (default: 48)")
    parser.add_argument("--delete", action="store_true", help="Delete flagged incidents from Postgres")
    args = parser.parse_args()

    asyncio.run(main(args.hours, args.delete))
