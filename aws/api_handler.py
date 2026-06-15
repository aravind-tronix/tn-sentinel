import base64
import json
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from boto3.dynamodb.conditions import Attr, Key
from fastapi import FastAPI, HTTPException, Query
from mangum import Mangum

from dynamo import incidents_table, item_to_response

app = FastAPI(title="TN Sentinel API")

# ── In-memory stats cache (5-minute TTL, lives for Lambda container lifetime) ──
_cache: dict = {}
CACHE_TTL = 300

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None

def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


# ── Cursor helpers ─────────────────────────────────────────────────────────────
def _decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.b64decode(cursor.encode()).decode())
    except Exception:
        return None

def _encode_cursor(lek: dict | None) -> str | None:
    if not lek:
        return None
    return base64.b64encode(json.dumps(lek).encode()).decode()


# ── Full exhaust (for stats/aggregations only — NOT for paginated incidents) ───
def _exhaust(table, op: str, kwargs: dict) -> list[dict]:
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tn-sentinel"}


# ── Incidents ──────────────────────────────────────────────────────────────────
@app.get("/incidents")
async def list_incidents(
    district: Optional[str]  = Query(None),
    category: Optional[str]  = Query(None),
    source_id: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str]   = Query(None),
    limit: int               = Query(20, ge=1, le=100),
    cursor: Optional[str]    = Query(None),
    min_confidence: float    = Query(0.0, ge=0.0, le=1.0),
    exclude_unknown: bool    = Query(False),
):
    table = incidents_table()
    lek   = _decode_cursor(cursor)
    extra = {"ExclusiveStartKey": lek} if lek else {}

    if district and category:
        cat = category.title()
        if from_date or to_date:
            sk_from = f"{cat}#{from_date or '0000'}"
            sk_to   = f"{cat}#{to_date or '9999'}"
            key_cond = Key("district").eq(district.lower()) & Key("cat_time").between(sk_from, sk_to)
        else:
            key_cond = Key("district").eq(district.lower()) & Key("cat_time").begins_with(f"{cat}#")
        resp = table.query(
            IndexName="district-category-time-index",
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit,
            **extra,
        )

    elif district:
        if from_date or to_date:
            sk_from  = from_date or "0000"
            sk_to    = (to_date or "9999") + "~"
            key_cond = Key("district").eq(district.lower()) & Key("published_at_id").between(sk_from, sk_to)
        else:
            key_cond = Key("district").eq(district.lower())
        resp = table.query(
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit,
            **extra,
        )

    elif category:
        key_cond = Key("category").eq(category.title())
        if from_date or to_date:
            key_cond = key_cond & Key("published_at").between(from_date or "0000", to_date or "9999")
        resp = table.query(
            IndexName="category-time-index",
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit,
            **extra,
        )

    elif source_id:
        key_cond = Key("source_id").eq(source_id)
        if from_date or to_date:
            key_cond = key_cond & Key("published_at").between(from_date or "0000", to_date or "9999")
        resp = table.query(
            IndexName="source-time-index",
            KeyConditionExpression=key_cond,
            ScanIndexForward=False,
            Limit=limit,
            **extra,
        )

    else:
        scan_kwargs: dict = {"Limit": limit, **extra}
        filters = []
        if from_date and to_date:
            filters.append(Attr("published_at").between(from_date, to_date))
        elif from_date:
            filters.append(Attr("published_at").gte(from_date))
        elif to_date:
            filters.append(Attr("published_at").lte(to_date))
        if filters:
            scan_kwargs["FilterExpression"] = filters[0]
        resp = table.scan(**scan_kwargs)

    items = resp.get("Items", [])
    next_cursor = _encode_cursor(resp.get("LastEvaluatedKey"))

    # Post-filters (applied after DynamoDB fetch)
    if min_confidence > 0:
        items = [i for i in items if float(i.get("confidence") or 0) >= min_confidence]
    if exclude_unknown:
        items = [i for i in items if (i.get("district") or "unknown") != "unknown"]

    return {
        "incidents": [item_to_response(i) for i in items],
        "next_cursor": next_cursor,
    }


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


# ── Stats ──────────────────────────────────────────────────────────────────────
def _norm_cat(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    return raw.title() if raw.islower() else raw


@app.get("/stats/kpis")
async def get_kpis():
    cached = _cache_get("kpis")
    if cached:
        return cached

    now          = datetime.utcnow()
    window_cur   = (now - timedelta(hours=24)).isoformat()
    window_prev  = (now - timedelta(hours=48)).isoformat()

    cur_resp  = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").gte(window_cur),
        "ProjectionExpression": "viral_score, source_id",
    })
    prev_resp = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").between(window_prev, window_cur),
        "ProjectionExpression": "viral_score",
    })

    scores_cur  = [int(i.get("viral_score") or 0) for i in cur_resp]
    scores_prev = [int(i.get("viral_score") or 0) for i in prev_resp]
    avg_cur     = sum(scores_cur)  / len(scores_cur)  if scores_cur  else 0.0
    avg_prev    = sum(scores_prev) / len(scores_prev) if scores_prev else 0.0
    cnt_cur     = len(cur_resp)
    cnt_prev    = len(prev_resp)

    result = {
        "events_24h":             cnt_cur,
        "avg_viral_score":        round(avg_cur, 2),
        "active_sources":         len({i.get("source_id") for i in cur_resp if i.get("source_id")}),
        "high_priority_incidents": sum(1 for s in scores_cur if s >= 80),
        "alerts":                 sum(1 for s in scores_cur if s >= 90),
        "events_24h_delta":       round((cnt_cur - cnt_prev) / cnt_prev * 100, 1) if cnt_prev else None,
        "avg_viral_score_delta":  round(avg_cur - avg_prev, 1) if avg_prev else None,
    }
    _cache_set("kpis", result)
    return result


@app.get("/stats/districts")
async def get_district_stats():
    cached = _cache_get("districts")
    if cached:
        return cached

    resp  = incidents_table().scan(ProjectionExpression="district, category")
    items = resp.get("Items", [])

    counts: dict     = {}
    cat_counts: dict = {}
    for item in items:
        d = item.get("district", "unknown")
        c = _norm_cat(item.get("category"))
        counts[d] = counts.get(d, 0) + 1
        cat_counts.setdefault(d, {})[c] = cat_counts.get(d, {}).get(c, 0) + 1

    result = [
        {"district": d, "count": n, "top_category": max(cat_counts[d], key=cat_counts[d].get)}
        for d, n in sorted(counts.items(), key=lambda x: -x[1])
    ]
    _cache_set("districts", result)
    return result


@app.get("/stats/categories")
async def get_category_stats():
    cached = _cache_get("categories")
    if cached:
        return cached

    resp  = incidents_table().scan(ProjectionExpression="category")
    items = resp.get("Items", [])
    counts: dict = {}
    for item in items:
        c = _norm_cat(item.get("category"))
        counts[c] = counts.get(c, 0) + 1

    result = [{"category": c, "count": n} for c, n in sorted(counts.items(), key=lambda x: -x[1])]
    _cache_set("categories", result)
    return result


@app.get("/stats/timeline")
async def get_timeline(days: int = Query(30, ge=0, le=2000), breakdown: bool = Query(False)):
    proj        = "published_at, category" if breakdown else "published_at"
    scan_kwargs: dict = {"ProjectionExpression": proj}
    if days > 0:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        scan_kwargs["FilterExpression"] = Attr("published_at").gte(since)
    items = _exhaust(incidents_table(), "scan", scan_kwargs)

    if breakdown:
        counts: dict = {}
        for item in items:
            pub     = item.get("published_at", "")
            raw_cat = item.get("category") or "Other"
            cat     = raw_cat.title() if raw_cat.islower() else raw_cat
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
    cached = _cache_get("sentiment")
    if cached:
        return cached

    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("sentiment").exists(),
        "ProjectionExpression": "sentiment",
    })
    counts: dict = {}
    for item in items:
        s = item.get("sentiment")
        if s:
            counts[s] = counts.get(s, 0) + 1

    result = [{"sentiment": s, "count": n} for s, n in sorted(counts.items(), key=lambda x: -x[1])]
    _cache_set("sentiment", result)
    return result


@app.get("/stats/sources")
async def get_source_stats():
    cached = _cache_get("sources")
    if cached:
        return cached

    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("source_name").exists(),
        "ProjectionExpression": "source_name",
    })
    counts: dict = {}
    for item in items:
        src = item.get("source_name")
        if src:
            counts[src] = counts.get(src, 0) + 1

    result = [{"source_name": s, "count": n} for s, n in sorted(counts.items(), key=lambda x: -x[1])[:10]]
    _cache_set("sources", result)
    return result


@app.get("/stats/viral-distribution")
async def get_viral_distribution():
    cached = _cache_get("viral_dist")
    if cached:
        return cached

    items   = _exhaust(incidents_table(), "scan", {"ProjectionExpression": "viral_score"})
    buckets = [(0, 20, "Low"), (20, 40, "Moderate"), (40, 60, "High"), (60, 80, "Critical"), (80, 101, "Viral")]
    counts  = {label: 0 for _, _, label in buckets}
    for item in items:
        score = int(item.get("viral_score") or 0)
        for lo, hi, label in buckets:
            if lo <= score < hi:
                counts[label] += 1
                break

    result = [{"bucket": label, "lo": lo, "count": counts[label]} for lo, _, label in buckets]
    _cache_set("viral_dist", result)
    return result


@app.get("/stats/trending")
async def get_trending(hours: int = Query(24, ge=1, le=168), limit: int = Query(5, ge=1, le=20)):
    """Top incidents by viral score in the last N hours."""
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").gte(since),
        "ProjectionExpression": "district, category, viral_score, title, published_at, #u, id, source_name, sentiment, confidence, source_id, #lang, entities, scraped_at, summary, image_url",
        "ExpressionAttributeNames": {"#u": "url", "#lang": "language"},
    })
    items.sort(key=lambda x: int(x.get("viral_score") or 0), reverse=True)
    return [item_to_response(i) for i in items[:limit]]


@app.get("/stats/sources/health")
async def get_source_health():
    """Last seen article time per source (derived from incidents table)."""
    since = (datetime.utcnow() - timedelta(days=14)).isoformat()
    items = _exhaust(incidents_table(), "scan", {
        "FilterExpression": Attr("published_at").gte(since),
        "ProjectionExpression": "source_id, source_name, published_at",
    })
    latest: dict = {}
    for item in items:
        src = item.get("source_id")
        pub = item.get("published_at", "")
        if src and pub:
            if src not in latest or pub > latest[src]["last_seen"]:
                latest[src] = {
                    "source_id":   src,
                    "source_name": item.get("source_name", src),
                    "last_seen":   pub,
                }
    return sorted(latest.values(), key=lambda x: x["last_seen"], reverse=True)


lambda_handler = Mangum(app, lifespan="off")
