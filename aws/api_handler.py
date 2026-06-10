import os
from datetime import datetime, timedelta
from typing import Optional

from boto3.dynamodb.conditions import Attr, Key
from fastapi import FastAPI, HTTPException, Query
from mangum import Mangum

from dynamo import incidents_table, item_to_response

# CORS is handled entirely by API Gateway HTTP API cors_configuration in Terraform.
# Do not add CORSMiddleware here — it would double-set headers and leak "*".
app = FastAPI(title="TN Sentinel API")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tn-sentinel"}


def _exhaust(table, op: str, kwargs: dict) -> list[dict]:
    """Paginate through DynamoDB query/scan until all matching items are returned."""
    fn = table.query if op == "query" else table.scan
    items = []
    while True:
        resp = fn(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs = {**kwargs, "ExclusiveStartKey": lek}
    return items


@app.get("/incidents")
async def list_incidents(
    district: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    table = incidents_table()

    if district and category:
        cat = category.title()
        if from_date or to_date:
            sk_from = f"{cat}#{from_date or '0000'}"
            sk_to = f"{cat}#{to_date or '9999'}"
            key_cond = Key("district").eq(district.lower()) & Key("cat_time").between(sk_from, sk_to)
        else:
            key_cond = Key("district").eq(district.lower()) & Key("cat_time").begins_with(f"{cat}#")
        all_items = _exhaust(table, "query", {
            "IndexName": "district-category-time-index",
            "KeyConditionExpression": key_cond,
            "ScanIndexForward": False,
        })

    elif district:
        if from_date or to_date:
            sk_from = from_date or "0000"
            sk_to = (to_date or "9999") + "~"
            key_cond = Key("district").eq(district.lower()) & Key("published_at_id").between(sk_from, sk_to)
        else:
            key_cond = Key("district").eq(district.lower())
        all_items = _exhaust(table, "query", {
            "KeyConditionExpression": key_cond,
            "ScanIndexForward": False,
        })

    elif category:
        key_cond = Key("category").eq(category.title())
        if from_date or to_date:
            key_cond = key_cond & Key("published_at").between(from_date or "0000", to_date or "9999")
        all_items = _exhaust(table, "query", {
            "IndexName": "category-time-index",
            "KeyConditionExpression": key_cond,
            "ScanIndexForward": False,
        })

    elif source_id:
        key_cond = Key("source_id").eq(source_id)
        if from_date or to_date:
            key_cond = key_cond & Key("published_at").between(from_date or "0000", to_date or "9999")
        all_items = _exhaust(table, "query", {
            "IndexName": "source-time-index",
            "KeyConditionExpression": key_cond,
            "ScanIndexForward": False,
        })

    else:
        scan_kwargs: dict = {}
        if from_date and to_date:
            scan_kwargs["FilterExpression"] = Attr("published_at").between(from_date, to_date)
        elif from_date:
            scan_kwargs["FilterExpression"] = Attr("published_at").gte(from_date)
        elif to_date:
            scan_kwargs["FilterExpression"] = Attr("published_at").lte(to_date)
        all_items = _exhaust(table, "scan", scan_kwargs)

    # Sort descending by published_at for consistent ordering
    all_items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    total = len(all_items)
    page_items = all_items[offset: offset + limit]
    return {"incidents": [item_to_response(i) for i in page_items], "total": total}


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: int):
    resp = incidents_table().query(
        IndexName="id-index",
        KeyConditionExpression=Key("id").eq(str(incident_id)),
        Limit=1,
    )
    items = resp.get("Items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Incident not found")
    return item_to_response(items[0])


@app.get("/stats/kpis")
async def get_kpis():
    now = datetime.utcnow()
    window_cur  = (now - timedelta(hours=24)).isoformat()
    window_prev = (now - timedelta(hours=48)).isoformat()

    cur_resp  = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").gte(window_cur),
        "ProjectionExpression": "viral_score, source_id",
    })
    prev_resp = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").between(window_prev, window_cur),
        "ProjectionExpression": "viral_score",
    })

    scores_cur  = [int(i.get("viral_score", 0)) for i in cur_resp]
    scores_prev = [int(i.get("viral_score", 0)) for i in prev_resp]

    avg_cur  = sum(scores_cur)  / len(scores_cur)  if scores_cur  else 0.0
    avg_prev = sum(scores_prev) / len(scores_prev) if scores_prev else 0.0
    cnt_cur  = len(cur_resp)
    cnt_prev = len(prev_resp)

    events_delta    = round((cnt_cur - cnt_prev) / cnt_prev * 100, 1) if cnt_prev else None
    avg_score_delta = round(avg_cur - avg_prev, 1) if avg_prev else None

    return {
        "events_24h": cnt_cur,
        "avg_viral_score": round(avg_cur, 2),
        "active_sources": len({i.get("source_id") for i in cur_resp if i.get("source_id")}),
        "high_priority_incidents": sum(1 for s in scores_cur if s >= 80),
        "alerts": sum(1 for s in scores_cur if s >= 90),
        "events_24h_delta": events_delta,
        "avg_viral_score_delta": avg_score_delta,
    }


@app.get("/stats/districts")
async def get_district_stats():
    resp = incidents_table().scan(ProjectionExpression="district, category")
    items = resp.get("Items", [])

    counts: dict = {}
    cat_counts: dict = {}
    for item in items:
        d = item.get("district", "unknown")
        c = _norm_cat(item.get("category"))
        counts[d] = counts.get(d, 0) + 1
        cat_counts.setdefault(d, {})[c] = cat_counts.get(d, {}).get(c, 0) + 1

    return [
        {"district": d, "count": n, "top_category": max(cat_counts[d], key=cat_counts[d].get)}
        for d, n in sorted(counts.items(), key=lambda x: -x[1])
    ]


def _norm_cat(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    return raw.title() if raw.islower() else raw


@app.get("/stats/categories")
async def get_category_stats():
    resp = incidents_table().scan(ProjectionExpression="category")
    items = resp.get("Items", [])

    counts: dict = {}
    for item in items:
        c = _norm_cat(item.get("category"))
        counts[c] = counts.get(c, 0) + 1

    return [{"category": c, "count": n} for c, n in sorted(counts.items(), key=lambda x: -x[1])]


@app.get("/stats/timeline")
async def get_timeline(days: int = Query(30, ge=7, le=90), breakdown: bool = Query(False)):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    proj = "published_at, category" if breakdown else "published_at"
    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").gte(since),
        "ProjectionExpression": proj,
    })

    if breakdown:
        counts: dict = {}
        for item in items:
            pub = item.get("published_at", "")
            raw_cat = item.get("category") or "Other"
            cat = raw_cat.title() if raw_cat.islower() else raw_cat
            if pub:
                key = (pub[:10], cat)
                counts[key] = counts.get(key, 0) + 1
        return [{"date": d, "category": c, "count": n} for (d, c), n in sorted(counts.items())]

    counts2: dict = {}
    for item in items:
        pub = item.get("published_at", "")
        if pub:
            counts2[pub[:10]] = counts2.get(pub[:10], 0) + 1
    return [{"date": d, "count": n} for d, n in sorted(counts2.items())]


@app.get("/stats/sentiment")
async def get_sentiment_stats():
    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("sentiment").exists(),
        "ProjectionExpression": "sentiment",
    })

    counts: dict = {}
    for item in items:
        s = item.get("sentiment")
        if s:
            counts[s] = counts.get(s, 0) + 1

    return [{"sentiment": s, "count": n} for s, n in sorted(counts.items(), key=lambda x: -x[1])]


@app.get("/stats/sources")
async def get_source_stats():
    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("source_name").exists(),
        "ProjectionExpression": "source_name",
    })

    counts: dict = {}
    for item in items:
        src = item.get("source_name")
        if src:
            counts[src] = counts.get(src, 0) + 1

    return [
        {"source_name": s, "count": n}
        for s, n in sorted(counts.items(), key=lambda x: -x[1])[:10]
    ]


@app.get("/stats/viral-distribution")
async def get_viral_distribution():
    items = _exhaust(incidents_table(), "scan", {
        "ProjectionExpression": "viral_score",
    })

    buckets = [(0, 20, "Low"), (20, 40, "Moderate"), (40, 60, "High"), (60, 80, "Critical"), (80, 101, "Viral")]
    counts = {label: 0 for _, _, label in buckets}
    for item in items:
        score = int(item.get("viral_score", 0))
        for lo, hi, label in buckets:
            if lo <= score < hi:
                counts[label] += 1
                break

    return [{"bucket": label, "lo": lo, "count": counts[label]} for lo, _, label in buckets]


lambda_handler = Mangum(app, lifespan="off")
