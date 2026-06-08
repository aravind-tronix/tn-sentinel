from sqlalchemy import Column, String, Integer, Float, Text, DateTime, Boolean, JSON, Index
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from pgvector.sqlalchemy import Vector
from datetime import datetime
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_settings

settings = get_settings()

class Base(DeclarativeBase):
    pass

class Incident(Base):
    __tablename__ = "incidents"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    url          = Column(String(2048), unique=True, nullable=False, index=True)
    title        = Column(String(512), nullable=False)
    summary      = Column(Text)
    raw_text     = Column(Text)
    source_id    = Column(String(64), index=True)
    source_name  = Column(String(128))
    district     = Column(String(64), index=True)
    category     = Column(String(64), index=True)
    viral_score  = Column(Integer, default=0)
    sentiment    = Column(String(16))
    language     = Column(String(8), default="en")
    confidence   = Column(Float, default=0.0)
    entities     = Column(JSON, default=dict)
    image_url    = Column(String(2048))
    published_at = Column(DateTime)
    scraped_at   = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, default=datetime.utcnow)
    embedding    = Column(Vector(768))

    __table_args__ = (
        Index("ix_incidents_district_category", "district", "category"),
        Index("ix_incidents_published_at", "published_at"),
        Index("ix_incidents_viral_score", "viral_score"),
    )

    def to_dict(self):
        return {
            "id":           self.id,
            "url":          self.url,
            "title":        self.title,
            "summary":      self.summary,
            "source_id":    self.source_id,
            "source_name":  self.source_name,
            "district":     self.district,
            "category":     self.category,
            "viral_score":  self.viral_score,
            "sentiment":    self.sentiment,
            "language":     self.language,
            "confidence":   self.confidence,
            "entities":     self.entities,
            "image_url":    self.image_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "scraped_at":   self.scraped_at.isoformat() if self.scraped_at else None,
        }

class ScrapeLog(Base):
    __tablename__ = "scrape_log"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    source_id         = Column(String(64), index=True)
    run_at            = Column(DateTime, default=datetime.utcnow)
    articles_found    = Column(Integer, default=0)
    articles_new      = Column(Integer, default=0)
    articles_filtered = Column(Integer, default=0)
    errors            = Column(Integer, default=0)
    duration_ms       = Column(Integer, default=0)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[DB] Tables initialized")

async def get_session():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
