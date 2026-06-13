"""Copy all items from prod DynamoDB table to dev."""
import boto3
from boto3.dynamodb.conditions import Attr

REGION = "ap-south-1"
SRC    = "tn-sentinel-incidents-prod"
DST    = "tn-sentinel-incidents-dev"

dynamodb = boto3.resource("dynamodb", region_name=REGION)
src = dynamodb.Table(SRC)
dst = dynamodb.Table(DST)

def scan_all(table):
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items

print(f"Scanning {SRC}...")
items = scan_all(src)
print(f"Found {len(items)} items. Writing to {DST}...")

written = 0
with dst.batch_writer() as batch:
    for item in items:
        batch.put_item(Item=item)
        written += 1
        if written % 100 == 0:
            print(f"  {written}/{len(items)}")

print(f"Done. Migrated {written} items to {DST}.")
