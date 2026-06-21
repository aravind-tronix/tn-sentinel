#!/usr/bin/env python3
"""
Re-process incidents with district='unknown' in a given DynamoDB table.
Tries NLP preprocessing first, then LLM district_chain as fallback.
DynamoDB district is the partition key, so fix = delete + re-insert.

Usage:
    ./venv/bin/python scripts/fix_unknown_districts.py --table tn-sentinel-incidents-prod
"""
import argparse
import asyncio
import sys
sys.path.insert(0, '.')

import boto3
from boto3.dynamodb.conditions import Attr

from local_server.pipeline.nlp_preprocessor import preprocess
from local_server.pipeline.llm_chains import process_article


async def fix_table(table_name: str, dry_run: bool = False):
    ddb   = boto3.resource('dynamodb', region_name='ap-south-1')
    table = ddb.Table(table_name)

    # Fetch all unknown-district items with full data
    print(f"Scanning {table_name} for district='unknown'...")
    items: list = []
    kwargs: dict = {'FilterExpression': Attr('district').eq('unknown')}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get('Items', []))
        lek = resp.get('LastEvaluatedKey')
        if not lek:
            break
        kwargs['ExclusiveStartKey'] = lek

    print(f"Found {len(items)} unknown-district incidents\n")

    fixed = 0
    skipped = 0

    for idx, item in enumerate(items, 1):
        title   = item.get('title', '') or ''
        summary = item.get('summary', '') or ''
        text    = f"{title}. {summary}".strip()

        # Stage 1 — NLP (preprocess expects a dict with 'text' and 'title')
        nlp      = preprocess({'text': text, 'title': title, 'url': item.get('url', '')})
        district = nlp.get('district') if nlp.get('district') != 'unknown' else None

        # Stage 2 — LLM district_chain
        if not district:
            try:
                from local_server.pipeline.llm_chains import district_chain, infer_district
                entities = item.get('entities', {})
                category = item.get('category') or 'unknown'
                district = await infer_district(text[:600], entities, category)
            except Exception:
                pass

        status = f"→ {district}" if district else "→ still unknown"
        print(f"[{idx:3}/{len(items)}] {title[:65]:<65} {status}")

        if district and not dry_run:
            old_key  = {'district': 'unknown', 'published_at_id': item['published_at_id']}
            new_item = {**item, 'district': district}
            # Rebuild cat_time composite key if present
            cat = new_item.get('category') or 'Other'
            pub = new_item.get('published_at') or ''
            new_item['cat_time'] = f"{cat}#{pub}"
            table.delete_item(Key=old_key)
            table.put_item(Item=new_item)
            fixed += 1
        elif district:
            fixed += 1  # dry-run count
        else:
            skipped += 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Fixed: {fixed} | Still unknown: {skipped}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--table', default='tn-sentinel-incidents-prod')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    asyncio.run(fix_table(args.table, dry_run=args.dry_run))
