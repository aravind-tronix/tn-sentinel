"""
LLM pipeline powered by the Claude Agent SDK.

Replaces Ollama triage + extraction chains with Claude Code running
programmatically.  Ollama is kept only for embeddings (nomic-embed-text).

Flow per article:
  preprocess (spaCy) → triage (Claude) → extract (Claude) → embed (Ollama)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from langchain_ollama import OllamaEmbeddings

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    RateLimitEvent,
    query,
)

from local_server.config import get_settings
from local_server.pipeline.nlp_preprocessor import (
    CITY_TO_DISTRICT,
    TN_DISTRICTS,
    preprocess,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_PROJECT_DIR = str(Path(__file__).parent.parent.parent)

# Ollama kept only for embeddings
embedder = OllamaEmbeddings(
    model=settings.ollama_embed_model,
    num_gpu=settings.ollama_num_gpu,
    base_url=settings.ollama_host,
    async_client_kwargs={"timeout": 120.0},
)

# ── Districts list (passed into prompts) ──────────────────────────────────────
_DISTRICTS = (
    "chennai, coimbatore, madurai, tiruchirappalli, salem, tirunelveli, vellore, "
    "erode, thoothukudi, dindigul, kanchipuram, krishnagiri, namakkal, theni, "
    "karur, dharmapuri, nilgiris, ariyalur, perambalur, cuddalore, villupuram, "
    "nagapattinam, thanjavur, tiruvarur, pudukkottai, sivaganga, virudhunagar, "
    "ramanathapuram, tenkasi, kanyakumari, tiruppur, ranipet, chengalpattu, "
    "tirupattur, kallakurichi, mayiladuthurai"
)

_CATEGORIES = "Homicide, Theft, Cybercrime, Assault, Narcotics, Road Accident, Sexual Offence, Fraud"

# ── System prompts ────────────────────────────────────────────────────────────

_TRIAGE_SYSTEM = f"""You are a Tamil Nadu crime news filter with one task: reply YES or NO.

Reply YES only when the article's PRIMARY subject is a specific, already-occurred
criminal incident in Tamil Nadu from one of these categories:
{_CATEGORIES}

Reply NO for:
- Court hearings, bail orders, verdicts, legal or procedural news
- Political statements, reactions, or opinions that reference past crimes
- Non-criminal accidents: fires, gas leaks, building collapses, floods, natural disasters
- Civic, health, administrative, infrastructure, or election news
- Awareness campaigns, crime-prevention schemes, or policy announcements
- Missing-persons alerts or general law-and-order round-ups with no specific incident

Reply with the single word YES or NO. Nothing else."""

_EXTRACTION_SYSTEM = f"""You are a Tamil Nadu crime intelligence analyst. Extract structured data from Tamil Nadu crime news articles with high precision.

VALID CATEGORIES (pick exactly one): {_CATEGORIES}
VALID DISTRICTS (lowercase): {_DISTRICTS}

Extraction rules:
- title: concise factual English title, max 12 words, no clickbait
- district: the specific TN district where the incident occurred (lowercase).
  Map cities/towns to their parent district (e.g. Coimbatore city → coimbatore).
  Use "unknown" only if genuinely unidentifiable.
- category: the primary crime type. If multiple crimes, choose the most serious.
- summary: 2-3 factual sentences — who/what/where/when/outcome.
- sentiment: almost always "negative" for crime; "neutral" only for procedural follow-up
- viral_score (0-100):
    Base 50 · +25 for Homicide or Sexual Offence · +15 for multiple victims or gang
    +10 for public official involved · +10 if < 6 h old · −10 if accused arrested.
    Clamp 0-100.
- confidence: 0.0-1.0 your confidence in the extraction accuracy.

Return ONLY a valid JSON object with these 7 keys — no markdown, no explanation."""

# JSON schema for structured output
_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title":       {"type": "string"},
        "district":    {"type": "string"},
        "category":    {"type": "string", "enum": _CATEGORIES.split(", ")},
        "summary":     {"type": "string"},
        "sentiment":   {"type": "string", "enum": ["negative", "neutral", "positive"]},
        "viral_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "confidence":  {"type": "number",  "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["title", "district", "category", "summary", "sentiment", "viral_score", "confidence"],
}

# ── SDK helpers ───────────────────────────────────────────────────────────────

def _base_options(**kwargs) -> ClaudeAgentOptions:
    """Common options — no filesystem tools, project working dir."""
    return ClaudeAgentOptions(
        allowed_tools=[],          # pure text completion, no file/bash tools
        cwd=_PROJECT_DIR,
        setting_sources=None,      # don't load any .claude/ config
        **kwargs,
    )


async def _run_query(prompt: str, options: ClaudeAgentOptions, timeout: float = 60.0) -> Optional[ResultMessage]:
    """Run a single query and return the ResultMessage, or None on error/timeout."""
    async def _inner():
        result = None
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, ResultMessage):
                result = msg
            elif isinstance(msg, RateLimitEvent):
                wait = getattr(msg, "retry_after_ms", 5000) / 1000
                logger.warning("Rate limit — waiting %.1fs", wait)
                await asyncio.sleep(wait)
        return result

    try:
        return await asyncio.wait_for(_inner(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Claude query timed out after %ss", timeout)
        return None
    except Exception as exc:
        logger.error("Claude query error: %s: %s", type(exc).__name__, str(exc)[:120])
        return None


# ── Stage 1: Triage ───────────────────────────────────────────────────────────

async def triage_article(text: str) -> bool:
    """Return True if the article describes a relevant Tamil Nadu crime incident."""
    options = _base_options(
        system_prompt=_TRIAGE_SYSTEM,
        max_turns=1,
        effort="low",       # binary decision — low effort is fast and sufficient
    )
    result = await _run_query(
        prompt=f"Triage this article:\n\n{text[:900]}",
        options=options,
        timeout=45.0,
    )
    if not result:
        return False
    answer = (result.result or "").strip().upper()
    return "YES" in answer and "NO" not in answer


# ── Stage 2: Extraction ───────────────────────────────────────────────────────

async def extract_incident(text: str, spacy_hints: dict) -> Optional[dict]:
    """Extract structured incident fields. Returns a dict or None."""
    # Inject spaCy NLP hints as context
    hints_lines = []
    if spacy_hints.get("detected_district"):
        hints_lines.append(f"spaCy detected district: {spacy_hints['detected_district']}")
    if spacy_hints.get("detected_category"):
        hints_lines.append(f"spaCy detected category: {spacy_hints['detected_category']}")
    if spacy_hints.get("entities"):
        hints_lines.append(f"Named entities: {spacy_hints['entities']}")
    hints_block = ("\n".join(hints_lines) + "\n\n") if hints_lines else ""

    options = _base_options(
        system_prompt=_EXTRACTION_SYSTEM,
        max_turns=1,
        effort="high",          # structured extraction benefits from higher effort
        output_format=_EXTRACTION_SCHEMA,
    )
    result = await _run_query(
        prompt=f"{hints_block}Article:\n{text[:4000]}",
        options=options,
        timeout=90.0,
    )
    if not result:
        return None

    # Prefer SDK structured output; fall back to JSON parsing from text
    if result.structured_output:
        return result.structured_output

    raw = (result.result or "").strip()
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    logger.warning("Extraction returned unparseable output: %s…", raw[:100])
    return None


# ── Stage 3: District fallback ────────────────────────────────────────────────

async def infer_district(text: str, entities: dict, detected_category: Optional[str]) -> Optional[str]:
    """Ask Claude to infer the TN district when spaCy couldn't find one."""
    options = _base_options(
        system_prompt=(
            f"You are a Tamil Nadu geography expert. "
            f"Given a crime article, reply with only the Tamil Nadu district name "
            f"in lowercase from this list:\n{_DISTRICTS}\n\n"
            f"If the district cannot be determined, reply with: unknown"
        ),
        max_turns=1,
        effort="low",
    )
    result = await _run_query(
        prompt=(
            f"Entities: {entities}\n"
            f"Category: {detected_category or 'unknown'}\n\n"
            f"Article:\n{text[:600]}"
        ),
        options=options,
        timeout=30.0,
    )
    if not result:
        return None
    raw = (result.result or "").strip().lower().strip(' .,"\'')
    if raw in ("", "unknown", "n/a"):
        return None
    return normalize_district(raw)


# ── District normalisation (unchanged from llm_chains) ────────────────────────

def normalize_district(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip().lower().strip(' .,"\'')
    if normalized in TN_DISTRICTS:
        return normalized
    if normalized in CITY_TO_DISTRICT and CITY_TO_DISTRICT[normalized] != "unknown":
        return CITY_TO_DISTRICT[normalized]
    for district in TN_DISTRICTS:
        if district in normalized:
            return district
    for city, district in CITY_TO_DISTRICT.items():
        if city in normalized and district != "unknown":
            return district
    return None


def parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            from dateparser import parse
            return parse(value)
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

async def process_article(raw_article: Dict) -> Optional[Dict]:
    """Full pipeline: spaCy → triage (Claude) → extract (Claude) → embed (Ollama).

    Same interface as the old llm_chains.process_article — worker.py is unchanged.
    """
    try:
        article = preprocess(raw_article)
        text = (article.get("text") or article.get("title") or "").strip()
        url = article.get("url", "unknown")[:100]

        logger.info("Processing: %s", url)

        if len(text) < 20:
            return None

        # ── Stage 1: Triage ──
        is_relevant = await triage_article(text)
        if not is_relevant:
            logger.info("Triage filtered: %s", url)
            return None
        logger.info("Triage passed: %s", url)

        # ── Stage 2: District inference (if spaCy missed it) ──
        spacy_hints = {
            "detected_district": article.get("detected_district"),
            "detected_category": article.get("detected_category"),
            "entities": article.get("entities", {}),
        }
        if not article.get("detected_district"):
            inferred = await infer_district(
                text[: settings.pipeline_max_text_length],
                article.get("entities", {}),
                article.get("detected_category"),
            )
            if inferred:
                article["detected_district"] = inferred
                spacy_hints["detected_district"] = inferred
                logger.info("District inferred: %s", inferred)

        # ── Stage 3: Extraction ──
        extracted = await extract_incident(
            text[: settings.pipeline_max_text_length],
            spacy_hints,
        )
        if not extracted:
            logger.warning("Extraction failed: %s", url)
            return None
        logger.info("Extraction complete: %s", url)

        # ── Stage 4: Embed (Ollama) ──
        embedding = None
        embed_text = f"{extracted.get('title', '')} {extracted.get('summary', '')}"
        try:
            embedding = await asyncio.wait_for(
                embedder.aembed_query(embed_text),
                timeout=120.0,
            )
        except Exception as e:
            logger.warning("Embedding failed: %s", str(e)[:80])

        # Resolve district
        district_raw = (extracted.get("district") or "").lower().strip()
        district_final = (
            normalize_district(district_raw)
            or normalize_district(article.get("detected_district") or "")
            or (district_raw if district_raw in TN_DISTRICTS else "unknown")
        )

        return {
            "source_id":        article.get("source_id"),
            "source_name":      article.get("source_name"),
            "url":              article.get("url"),
            "title":            extracted.get("title", article.get("title", "")),
            "summary":          extracted.get("summary"),
            "raw_text":         article.get("text"),
            "district":         district_final,
            "category":         extracted.get("category"),
            "viral_score":      int(extracted.get("viral_score", 0)),
            "sentiment":        extracted.get("sentiment"),
            "confidence":       float(extracted.get("confidence", 0.0)),
            "language":         article.get("language", "en"),
            "entities":         article.get("entities", {}),
            "detected_district": article.get("detected_district"),
            "detected_category": article.get("detected_category"),
            "embedding":        embedding,
            "published_at":     parse_datetime(article.get("published_at")),
            "scraped_at":       parse_datetime(article.get("scraped_at")),
            "processed_at":     datetime.utcnow(),
            "image_url":        article.get("image_url"),
        }

    except Exception as exc:
        logger.error("Processing error: %s: %s", type(exc).__name__, str(exc)[:120])
        return None


print("✅ Claude Agent SDK chains loaded")
