"""
For incidents flagged by the stricter triage prompt, decide whether they are a
genuine crime incident that just needs its category fixed, or not a crime
report at all (political reaction, court procedure, non-criminal accident) and
should be deleted from Postgres.

Usage:
  ./venv/bin/python scripts/cleanup_flagged.py --hours 72            # dry run, report only
  ./venv/bin/python scripts/cleanup_flagged.py --hours 72 --apply    # write fixes + deletions
"""

import asyncio
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from langchain_core.prompts import PromptTemplate

from local_server.db.models import AsyncSessionLocal, Incident, init_db
from local_server.pipeline.llm_chains import triage_chain, extractor_model

VALID_CATEGORIES = ["Homicide", "Theft", "Cybercrime", "Assault", "Narcotics", "Road Accident", "Sexual Offence", "Fraud"]

RECLASSIFY_PROMPT = PromptTemplate(
    input_variables=["text"],
    template="""You are auditing a Tamil Nadu crime intelligence database for misclassified entries.

This article was flagged as NOT primarily about a crime incident. Decide:

- If the article's main subject is genuinely a specific crime incident (even if buried under commentary/political reaction/court procedure) that fits one of these categories: Homicide, Theft, Cybercrime, Assault, Narcotics, Road Accident, Sexual Offence, Fraud — reply with ONLY that category name.
- If the article is purely political commentary/reaction, a court hearing/legal procedure not describing the crime itself, a non-criminal accident (fire, explosion, structural issue, natural disaster), civic/administrative/health news, or otherwise not a specific crime report — reply with exactly: NONE

Article:
{text}

Reply with only the category name or NONE.""",
)
reclassify_chain = RECLASSIFY_PROMPT | extractor_model


async def main(hours: int, apply: bool) -> None:
    await init_db()
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Incident).where(Incident.processed_at >= cutoff).order_by(Incident.processed_at.desc())
        )
        incidents = result.scalars().all()

    print(f"🔎 Scanning {len(incidents)} incidents processed in the last {hours}h\n")

    to_delete = []
    to_fix = []

    for inc in incidents:
        text = (inc.raw_text or inc.summary or inc.title or "").strip()
        if not text:
            continue
        try:
            triage_result = await asyncio.wait_for(triage_chain.ainvoke({"text": text[:800]}), timeout=120.0)
        except Exception as e:
            print(f"  ⚠️ triage error id={inc.id}: {e}")
            continue

        verdict = str(triage_result).strip().upper()
        is_yes = "YES" in verdict and "NO" not in verdict
        if is_yes:
            continue  # passes the new triage, leave category alone

        try:
            reclass_result = await asyncio.wait_for(
                reclassify_chain.ainvoke({"text": text[:2000]}), timeout=120.0
            )
        except Exception as e:
            print(f"  ⚠️ reclassify error id={inc.id}: {e}")
            continue

        verdict2 = str(reclass_result).strip()
        matched = next((c for c in VALID_CATEGORIES if c.lower() == verdict2.lower()), None)

        if matched:
            print(f"  🔧 id={inc.id} [{inc.category} -> {matched}] {inc.title}")
            to_fix.append((inc, matched))
        else:
            print(f"  🗑️  id={inc.id} [{inc.category}] {inc.title}  (not a crime report)")
            to_delete.append(inc)

    print(f"\n📋 {len(to_fix)} reclassified, {len(to_delete)} flagged for deletion (of {len(incidents)} scanned)")

    if apply:
        async with AsyncSessionLocal() as session:
            for inc, new_cat in to_fix:
                obj = await session.get(Incident, inc.id)
                if obj:
                    obj.category = new_cat
            for inc in to_delete:
                obj = await session.get(Incident, inc.id)
                if obj:
                    await session.delete(obj)
            await session.commit()
        print(f"✅ Applied: {len(to_fix)} category fixes, {len(to_delete)} deletions.")
    else:
        print("ℹ️  Re-run with --apply to write these changes to Postgres.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reclassify or delete flagged incidents")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    asyncio.run(main(args.hours, args.apply))
