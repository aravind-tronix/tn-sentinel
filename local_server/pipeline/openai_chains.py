"""
LLM pipeline powered by OpenAI Chat Completions via httpx.

Flow per article:
  preprocess (spaCy) → triage (OpenAI) → extract (OpenAI) → validate (OpenAI)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

import httpx

from local_server.config import get_settings
from local_server.pipeline.nlp_preprocessor import (
    CITY_TO_DISTRICT,
    TN_DISTRICTS,
    preprocess,
)

settings = get_settings()
logger = logging.getLogger(__name__)

_missing_key_logged = False

_DISTRICTS = (
    "chennai, coimbatore, madurai, tiruchirappalli, salem, tirunelveli, vellore, "
    "erode, thoothukudi, dindigul, kanchipuram, krishnagiri, namakkal, theni, "
    "karur, dharmapuri, nilgiris, ariyalur, perambalur, cuddalore, villupuram, "
    "nagapattinam, thanjavur, tiruvarur, pudukkottai, sivaganga, virudhunagar, "
    "ramanathapuram, tenkasi, kanyakumari, tiruppur, ranipet, chengalpattu, "
    "tirupattur, kallakurichi, mayiladuthurai"
)

_CATEGORIES = "Homicide, Theft, Cybercrime, Assault, Narcotics, Road Accident, Sexual Offence, Fraud"

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

_VALIDATOR_SYSTEM = f"""You are a Tamil Nadu crime intelligence validator.

You receive the original article and the extracted incident JSON.
Check each field strictly:

1. district — must be one of the 38 official TN districts in lowercase.
   Map city/town names to their parent district (e.g. Hosur → krishnagiri, Ooty → nilgiris).
   Valid districts: {_DISTRICTS}
2. category — must exactly match the primary crime type from: {_CATEGORIES}
3. viral_score — calibrate against severity (murder/rape=70+, major theft/narcotics=50-65, minor theft=20-40).
4. title — must be factual and max 12 words.
5. summary — must be 2-3 factual sentences, no speculation.

Return ONLY a JSON object — no markdown, no explanation:
- If all correct: {{"valid": true}}
- If corrections needed: {{"corrections": {{"field_name": "corrected_value"}}}}"""


def _json_from_text(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                return None
    return None


def _using_local_proxy() -> bool:
    base_url = (settings.openai_base_url or "").strip().lower()
    return base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")


def _llm_auth_available() -> bool:
    return bool((settings.openai_api_key or "").strip()) or _using_local_proxy()


async def _openai_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    timeout: float = 60.0,
) -> Optional[str]:
    """Call OpenAI Chat Completions. Returns assistant text or None on failure."""
    global _missing_key_logged

    api_key = (settings.openai_api_key or "").strip()
    if not api_key:
        if _using_local_proxy():
            api_key = "hermes-proxy"
        elif not _missing_key_logged:
            logger.error("OPENAI_API_KEY is not configured; OpenAI LLM pipeline disabled")
            _missing_key_logged = True
            return None
        else:
            return None

    payload = {
        "model": settings.openai_model,
        "temperature": settings.openai_temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        logger.error("OpenAI HTTP error: status=%s body=%s", exc.response.status_code, body)
    except Exception as exc:
        logger.error("OpenAI query error: %s: %s", type(exc).__name__, str(exc)[:180])
    return None


async def triage_article(text: str) -> tuple[bool, Optional[str]]:
    """Return (is_crime, provider_session_id)."""
    result = await _openai_chat(
        _TRIAGE_SYSTEM,
        f"Triage this article:\n\n{text[:900]}",
        timeout=45.0,
    )
    if not result:
        return False, None
    answer = result.strip().upper()
    return ("YES" in answer and "NO" not in answer), "openai"


async def _validate_extraction(article_text: str, extracted: dict) -> Optional[dict]:
    raw = await _openai_chat(
        _VALIDATOR_SYSTEM,
        f"Article:\n{article_text[:2000]}\n\nExtracted JSON:\n{json.dumps(extracted, indent=2)}",
        json_mode=True,
        timeout=60.0,
    )
    parsed = _json_from_text(raw or "")
    if not parsed or parsed.get("valid"):
        return None
    return parsed.get("corrections") or None


async def extract_incident(text: str, spacy_hints: dict) -> tuple[Optional[dict], Optional[str]]:
    hints_lines = []
    if spacy_hints.get("detected_district"):
        hints_lines.append(f"spaCy detected district: {spacy_hints['detected_district']}")
    if spacy_hints.get("detected_category"):
        hints_lines.append(f"spaCy detected category: {spacy_hints['detected_category']}")
    if spacy_hints.get("entities"):
        hints_lines.append(f"Named entities: {spacy_hints['entities']}")
    hints_block = ("\n".join(hints_lines) + "\n\n") if hints_lines else ""

    raw = await _openai_chat(
        _EXTRACTION_SYSTEM,
        f"{hints_block}Article:\n{text[:4000]}",
        json_mode=True,
        timeout=90.0,
    )
    extracted = _json_from_text(raw or "")
    if not extracted:
        logger.warning("Extraction returned unparseable OpenAI output: %s…", (raw or "")[:100])
        return None, "openai"

    corrections = await _validate_extraction(text, extracted)
    if corrections:
        logger.info("Validator corrected fields: %s", list(corrections.keys()))
        extracted = {**extracted, **corrections}

    return extracted, "openai"


async def infer_district(text: str, entities: dict, detected_category: Optional[str]) -> Optional[str]:
    raw = await _openai_chat(
        (
            f"You are a Tamil Nadu geography expert. Given a crime article, reply with only "
            f"the Tamil Nadu district name in lowercase from this list:\n{_DISTRICTS}\n\n"
            f"If the district cannot be determined, reply with: unknown"
        ),
        f"Entities: {entities}\nCategory: {detected_category or 'unknown'}\n\nArticle:\n{text[:600]}",
        timeout=30.0,
    )
    if not raw:
        return None
    cleaned = raw.strip().lower().strip(' .,"\'')
    if cleaned in ("", "unknown", "n/a"):
        return None
    return normalize_district(cleaned)


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


async def process_article(raw_article: Dict) -> Optional[Dict]:
    """Full pipeline: spaCy → triage (OpenAI) → district inference → extract."""
    try:
        if not _llm_auth_available():
            await _openai_chat("", "")
            return None

        article = preprocess(raw_article)
        text = (article.get("text") or article.get("title") or "").strip()
        url = article.get("url", "unknown")[:100]

        logger.info("Processing: %s", url)

        if len(text) < 20:
            return None

        is_relevant, triage_session = await triage_article(text)
        logger.info("Triage provider=%s url=%s verdict=%s", triage_session, url, "YES" if is_relevant else "NO")
        if not is_relevant:
            return None

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

        extracted, extract_session = await extract_incident(
            text[: settings.pipeline_max_text_length],
            spacy_hints,
        )
        if not extracted:
            logger.warning("Extraction failed provider=%s url=%s", extract_session, url)
            return None
        logger.info(
            "Extraction complete provider=%s url=%s category=%s district=%s",
            extract_session,
            url,
            extracted.get("category"),
            extracted.get("district"),
        )

        district_raw = (extracted.get("district") or "").lower().strip()
        district_final = (
            normalize_district(district_raw)
            or normalize_district(article.get("detected_district") or "")
            or (district_raw if district_raw in TN_DISTRICTS else "unknown")
        )

        return {
            "source_id": article.get("source_id"),
            "source_name": article.get("source_name"),
            "url": article.get("url"),
            "title": extracted.get("title", article.get("title", "")),
            "summary": extracted.get("summary"),
            "raw_text": article.get("text"),
            "district": district_final,
            "category": extracted.get("category"),
            "viral_score": int(extracted.get("viral_score", 0)),
            "sentiment": extracted.get("sentiment"),
            "confidence": float(extracted.get("confidence", 0.0)),
            "language": article.get("language", "en"),
            "entities": article.get("entities", {}),
            "detected_district": article.get("detected_district"),
            "detected_category": article.get("detected_category"),
            "embedding": None,
            "published_at": parse_datetime(article.get("published_at")),
            "scraped_at": parse_datetime(article.get("scraped_at")),
            "processed_at": datetime.utcnow(),
            "image_url": article.get("image_url"),
        }

    except Exception as exc:
        logger.error("Processing error: %s: %s", type(exc).__name__, str(exc)[:120])
        return None


print("✅ OpenAI chains loaded")
