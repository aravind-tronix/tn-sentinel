import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from local_server.api.broadcaster import EventBroadcaster
from local_server.api.schemas import (
    ArticleRequest,
    AnalyzeRequest,
    AnalyzeResponse,
    CategoryStat,
    DistrictStat,
    EnrichResponse,
    IncidentListResponse,
    IncidentResponse,
    KPIStats,
)
from local_server.config import get_settings
from local_server.db.models import AsyncSessionLocal, Incident
from local_server.pipeline.worker import process_and_save
from local_server.pipeline.llm_chains import process_article as process_article_raw

settings = get_settings()
app = FastAPI(title="Tamil Nadu Crime Intelligence API")

broadcaster = EventBroadcaster()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins.split(","),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-api-key"],
)


@app.on_event("startup")
async def startup_event() -> None:
    from local_server.db.models import init_db

    await init_db()


def get_api_key(x_api_key: str = Header(...)) -> str:
    if x_api_key != settings.llm_api_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_api_key


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "tn-crime-intel"}


@app.post("/enrich", response_model=EnrichResponse)
async def enrich_article(
    article: ArticleRequest,
    api_key: str = Depends(get_api_key),
) -> EnrichResponse:
    incident, created = await process_and_save(article.dict())
    if incident is None:
        return EnrichResponse(status="filtered", incident=None, created=False, reason="not crime related")

    enriched = IncidentResponse.from_orm(incident)
    if created:
        await broadcaster.publish({"type": "incident", "incident": enriched.dict()})

    return EnrichResponse(status="ok", incident=enriched.dict(), created=created)


@app.post("/broadcast")
async def broadcast_event(event: dict, api_key: str = Depends(get_api_key)) -> dict:
    await broadcaster.publish(event)
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_text(
    request: AnalyzeRequest,
    api_key: str = Depends(get_api_key),
) -> AnalyzeResponse:
    incident_data = await process_article_raw(
        {
            "source_id": "adhoc",
            "source_name": "adhoc",
            "title": request.title or request.text[:100],
            "text": request.text,
            "summary": None,
            "language": request.language or "en",
            "url": request.url or "https://example.com/adhoc",
            "published_at": None,
            "scraped_at": None,
            "image_url": None,
            "tags": [],
        }
    )
    if not incident_data:
        return AnalyzeResponse(status="filtered", incident=None)
    return AnalyzeResponse(status="ok", incident=incident_data)


@app.get("/incidents", response_model=IncidentListResponse)
async def list_incidents(
    district: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> IncidentListResponse:
    filters = []
    if district:
        filters.append(func.lower(Incident.district) == district.lower())
    if category:
        filters.append(func.lower(Incident.category) == category.lower())
    if source_id:
        filters.append(func.lower(Incident.source_id) == source_id.lower())
    if q:
        ilike_value = f"%{q}%"
        filters.append(
            Incident.title.ilike(ilike_value)
            | Incident.summary.ilike(ilike_value)
            | Incident.raw_text.ilike(ilike_value)
        )
    if from_date:
        try:
            filters.append(Incident.published_at >= datetime.fromisoformat(from_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format")
    if to_date:
        try:
            filters.append(Incident.published_at <= datetime.fromisoformat(to_date))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format")

    count_stmt = select(func.count()).select_from(Incident)
    data_stmt = select(Incident)
    for f in filters:
        count_stmt = count_stmt.where(f)
        data_stmt = data_stmt.where(f)

    total_result, rows = await asyncio.gather(
        db.execute(count_stmt),
        db.execute(data_stmt.order_by(Incident.published_at.desc().nullslast()).offset(offset).limit(limit)),
    )
    total = total_result.scalar() or 0
    incidents = [IncidentResponse.from_orm(row) for row in rows.scalars().all()]
    return IncidentListResponse(incidents=incidents, total=total)


@app.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(incident_id: int, db: AsyncSession = Depends(get_db)) -> IncidentResponse:
    stmt = select(Incident).where(Incident.id == incident_id)
    row = await db.execute(stmt)
    incident = row.scalars().first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentResponse.from_orm(incident)


@app.get("/stats/kpis", response_model=KPIStats)
async def get_kpis(db: AsyncSession = Depends(get_db)) -> KPIStats:
    now = datetime.utcnow()
    window_cur  = now - timedelta(hours=24)
    window_prev = now - timedelta(hours=48)

    def _where_cur(stmt):  return stmt.where(Incident.published_at >= window_cur)
    def _where_prev(stmt): return stmt.where(Incident.published_at.between(window_prev, window_cur))

    results = await asyncio.gather(
        db.execute(_where_cur(select(func.count()))),
        db.execute(_where_cur(select(func.coalesce(func.avg(Incident.viral_score), 0.0)))),
        db.execute(_where_cur(select(func.count(func.distinct(Incident.source_id))))),
        db.execute(_where_cur(select(func.count()).where(Incident.viral_score >= 80))),
        db.execute(_where_cur(select(func.count()).where(Incident.viral_score >= 90))),
        db.execute(_where_prev(select(func.count()))),
        db.execute(_where_prev(select(func.coalesce(func.avg(Incident.viral_score), 0.0)))),
    )

    events_24h        = int(results[0].scalar_one())
    avg_viral_score   = float(results[1].scalar_one() or 0.0)
    active_sources    = int(results[2].scalar_one())
    high_priority     = int(results[3].scalar_one())
    alerts            = int(results[4].scalar_one())
    events_prev       = int(results[5].scalar_one())
    avg_score_prev    = float(results[6].scalar_one() or 0.0)

    events_delta     = round((events_24h - events_prev) / events_prev * 100, 1) if events_prev else None
    avg_score_delta  = round(avg_viral_score - avg_score_prev, 1) if avg_score_prev else None

    return KPIStats(
        events_24h=events_24h,
        avg_viral_score=round(avg_viral_score, 2),
        active_sources=active_sources,
        high_priority_incidents=high_priority,
        alerts=alerts,
        events_24h_delta=events_delta,
        avg_viral_score_delta=avg_score_delta,
    )


@app.get("/stats/districts", response_model=list[DistrictStat])
async def get_district_stats(db: AsyncSession = Depends(get_db)) -> list[DistrictStat]:
    stmt = (
        select(Incident.district, func.count().label("count"))
        .group_by(Incident.district)
        .order_by(desc("count"))
        .limit(40)
    )
    rows = await db.execute(stmt)
    counts = rows.all()

    category_stmt = (
        select(Incident.district, Incident.category, func.count().label("count"))
        .group_by(Incident.district, Incident.category)
        .order_by(Incident.district, desc("count"))
    )
    category_rows = await db.execute(category_stmt)

    top_category: dict[Optional[str], str] = {}
    for district, category, count in category_rows:
        if district not in top_category:
            top_category[district] = category

    return [
        DistrictStat(district=district, count=int(count), top_category=top_category.get(district))
        for district, count in counts
    ]


@app.get("/stats/categories", response_model=list[CategoryStat])
async def get_category_stats(db: AsyncSession = Depends(get_db)) -> list[CategoryStat]:
    stmt = (
        select(Incident.category, func.count().label("count"))
        .group_by(Incident.category)
        .order_by(desc("count"))
    )
    rows = await db.execute(stmt)
    return [CategoryStat(category=category, count=int(count)) for category, count in rows.all()]


@app.get("/stream")
async def stream_events(request: Request) -> StreamingResponse:
    queue = await broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                payload = json.dumps(event, default=str)
                yield f"event: incident\ndata: {payload}\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
