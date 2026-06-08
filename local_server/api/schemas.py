from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl

class AuthHeader(BaseModel):
    x_api_key: str = Field(..., alias="x-api-key")

class ArticleRequest(BaseModel):
    source_id: str
    source_name: str
    title: str
    text: str
    summary: Optional[str] = None
    language: str = "en"
    url: HttpUrl
    published_at: Optional[datetime] = None
    scraped_at: Optional[datetime] = None
    image_url: Optional[HttpUrl] = None
    tags: Optional[List[str]] = None

class AnalyzeRequest(BaseModel):
    title: Optional[str] = None
    text: str
    language: Optional[str] = "en"
    url: Optional[HttpUrl] = None

class IncidentResponse(BaseModel):
    id: int
    url: str
    title: str
    summary: Optional[str]
    source_id: Optional[str]
    source_name: Optional[str]
    district: Optional[str]
    category: Optional[str]
    viral_score: int
    sentiment: Optional[str]
    language: Optional[str]
    confidence: float
    entities: Dict[str, Any]
    image_url: Optional[str]
    published_at: Optional[datetime]
    scraped_at: Optional[datetime]

    class Config:
        from_attributes = True

class IncidentListResponse(BaseModel):
    incidents: List[IncidentResponse]
    total: int = 0

class KPIStats(BaseModel):
    events_24h: int
    avg_viral_score: float
    active_sources: int
    high_priority_incidents: int
    alerts: int

class DistrictStat(BaseModel):
    district: Optional[str]
    count: int
    top_category: Optional[str]

class CategoryStat(BaseModel):
    category: Optional[str]
    count: int

class AnalyzeResponse(BaseModel):
    status: str
    incident: Optional[Dict[str, Any]]

class EnrichResponse(BaseModel):
    status: str
    incident: Optional[Dict[str, Any]]
    created: bool
    reason: Optional[str] = None
