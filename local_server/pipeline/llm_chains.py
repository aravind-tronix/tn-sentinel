from datetime import datetime
from typing import Dict, Optional
import asyncio

from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from local_server.config import get_settings
from local_server.pipeline.nlp_preprocessor import TN_DISTRICTS, CITY_TO_DISTRICT

settings = get_settings()

# ========================= MODELS =========================
triage_model = OllamaLLM(
    model=settings.ollama_triage_model,
    temperature=settings.ollama_temperature,
    num_ctx=1024,
    num_gpu=settings.ollama_num_gpu,
    base_url=settings.ollama_host,
    async_client_kwargs={"timeout": 120.0},
)

extractor_model = OllamaLLM(
    model=settings.ollama_extractor_model,
    temperature=settings.ollama_temperature,
    num_ctx=settings.ollama_num_ctx,
    num_gpu=settings.ollama_num_gpu,
    base_url=settings.ollama_host,
    async_client_kwargs={"timeout": 240.0},
)

embedder = OllamaEmbeddings(
    model=settings.ollama_embed_model,
    num_gpu=settings.ollama_num_gpu,
    base_url=settings.ollama_host,
    async_client_kwargs={"timeout": 120.0},
)

# ========================= OUTPUT SCHEMA =========================
class IncidentExtraction(BaseModel):
    title: str = Field(description="Short English title, max 12 words")
    district: str = Field(description="TN district name in lowercase")
    category: str = Field(description="One of: Homicide, Theft, Cybercrime, Assault, Narcotics, Road Accident, Sexual Offence, Fraud")
    viral_score: int = Field(description="Score between 0 and 100")
    summary: str = Field(description="2-sentence professional summary")
    sentiment: str = Field(description="positive / neutral / negative")
    confidence: float = Field(description="Confidence score 0.0-1.0")

parser = PydanticOutputParser(pydantic_object=IncidentExtraction)

# ========================= PROMPTS =========================
TRIAGE_PROMPT = PromptTemplate(
    input_variables=["text"],
    template="""Is this article about a crime or public safety incident in Tamil Nadu, India?
Reply with only YES or NO.

Article: {text}""",
)

EXTRACT_PROMPT = PromptTemplate(
    input_variables=["text", "detected_district", "detected_category", "entities", "format_instructions"],
    template="""You are an experienced Tamil Nadu Crime Intelligence Analyst.

NLP Hints (use but override if incorrect):
- Detected District: {detected_district}
- Detected Category: {detected_category}
- Entities: {entities}

Valid Districts (lowercase): chennai, coimbatore, madurai, tiruchirappalli, salem, tirunelveli, vellore, erode, thoothukudi, dindigul, kanchipuram, krishnagiri, namakkal, theni, karur, dharmapuri, nilgiris, ariyalur, perambalur, cuddalore, villupuram, nagapattinam, thanjavur, tiruvarur, pudukkottai, sivaganga, virudhunagar, ramanathapuram, tenkasi, kanyakumari, tiruppur, ranipet, chengalpattu, tirupattur, kallakurichi, mayiladuthurai
Valid Categories: Homicide, Theft, Cybercrime, Assault, Narcotics, Road Accident, Sexual Offence, Fraud

Viral Score Guidelines (0-100):
- Base: 50
- Homicide or Sexual Offence: +25
- Multiple victims or Gang involved: +15
- Public official / Politician involved: +10
- Very recent (< 6 hours): +10
- If accused arrested or incident resolved, subtract 10.
- Clamp final score between 0-100.

Article:
{text}

{format_instructions}""",
)

DISTRICT_PROMPT = PromptTemplate(
    input_variables=["text", "entities", "detected_category"],
    template="""You are a Tamil Nadu location specialist.

Read the article and determine the Tamil Nadu district for the incident.
If a city or town is mentioned, map it to the correct Tamil Nadu district.
If there is no clear Tamil Nadu district in the article, reply with unknown.

Valid district names (lowercase): chennai, coimbatore, madurai, tiruchirappalli, salem, tirunelveli, vellore, erode, thoothukudi, dindigul, kanchipuram, krishnagiri, namakkal, theni, karur, dharmapuri, nilgiris, ariyalur, perambalur, cuddalore, villupuram, nagapattinam, thanjavur, tiruvarur, pudukkottai, sivaganga, virudhunagar, ramanathapuram, tenkasi, kanyakumari, tiruppur, ranipet, chengalpattu, tirupattur, kallakurichi, mayiladuthurai

Article:
{text}

Entities: {entities}
Detected Category: {detected_category}

Reply with only one district name in lowercase, or unknown.""",
)

triage_chain = TRIAGE_PROMPT | triage_model
extraction_chain = EXTRACT_PROMPT | extractor_model | parser
district_chain = DISTRICT_PROMPT | extractor_model


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


async def infer_district(text: str, entities: Dict, detected_category: Optional[str]) -> Optional[str]:
    try:
        district_result = await asyncio.wait_for(
            district_chain.ainvoke({
                "text": text,
                "entities": str(entities),
                "detected_category": detected_category or "unknown",
            }),
            timeout=180.0,
        )
    except asyncio.TimeoutError:
        print("⏱️  District inference timeout")
        return None
    except Exception as e:
        print(f"❌ District inference error: {type(e).__name__}: {str(e)[:100]}")
        return None

    if district_result is None:
        return None

    district_text = str(district_result).strip()
    for line in district_text.splitlines():
        candidate = normalize_district(line)
        if candidate:
            return candidate
    return normalize_district(district_text)


print("✅ LLM Chains loaded successfully")


def parse_datetime(value: Optional[str | datetime]) -> Optional[datetime]:
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
    """Full pipeline: preprocess → triage → extract → embed"""
    from .nlp_preprocessor import preprocess

    try:
        article = preprocess(raw_article)
        text = (article.get("text") or article.get("title") or "").strip()
        print(f"🔎 Processing article: {article.get('url', 'unknown')[:100]}")
        print("📝 Scraped text after scraping:")
        print(text)
        print("--- end scraped text ---")

        if len(text) < 20:
            return None

        # Triage with timeout
        try:
            triage_result = await asyncio.wait_for(
                triage_chain.ainvoke({"text": text[:800]}),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            print(f"⏱️  Triage timeout for: {article.get('url', 'unknown')[:80]}")
            return None

        triage_text = str(triage_result).strip().upper()
        if "NO" in triage_text and "YES" not in triage_text:
            return None

        print("✅ Triage passed")

        if not article.get("detected_district"):
            inferred_district = await infer_district(
                text[: settings.pipeline_max_text_length],
                article.get("entities", {}),
                article.get("detected_category"),
            )
            if inferred_district:
                article["detected_district"] = inferred_district
                print(f"🔧 Inferred district from Ollama: {inferred_district}")

        # Extraction with timeout
        try:
            extracted = await asyncio.wait_for(
                extraction_chain.ainvoke({
                    "text": text[: settings.pipeline_max_text_length],
                    "detected_district": (article.get("detected_district") or "unknown").lower(),
                    "detected_category": (article.get("detected_category") or "unknown"),
                    "entities": str(article.get("entities", {})),
                    "format_instructions": parser.get_format_instructions(),
                }),
                timeout=420.0,
            )
        except asyncio.TimeoutError:
            print(f"⏱️  Extraction timeout for: {article.get('url', 'unknown')[:80]}")
            return None
        except Exception as e:
            print(f"❌ Extraction parse error: {type(e).__name__}: {str(e)[:100]}")
            return None

        print("✅ Extraction complete")

        embedding_text = f"{extracted.title} {extracted.summary}"
        try:
            embedding = await asyncio.wait_for(
                embedder.aembed_query(embedding_text),
                timeout=150.0,
            )
        except asyncio.TimeoutError:
            print(f"⏱️  Embedding timeout for: {article.get('url', 'unknown')[:80]}")
            return None
        except Exception as e:
            print(f"❌ Embedding error: {type(e).__name__}: {str(e)[:100]}")
            return None

        incident = {
            "source_id": article.get("source_id"),
            "source_name": article.get("source_name"),
            "url": article.get("url"),
            "title": extracted.title,
            "summary": extracted.summary,
            "raw_text": article.get("text"),
            "district": extracted.district.lower(),
            "category": extracted.category,
            "viral_score": extracted.viral_score,
            "sentiment": extracted.sentiment,
            "confidence": extracted.confidence,
            "language": article.get("language", "en"),
            "entities": article.get("entities", {}),
            "detected_district": article.get("detected_district"),
            "detected_category": article.get("detected_category"),
            "embedding": embedding,
            "published_at": parse_datetime(article.get("published_at")),
            "scraped_at": parse_datetime(article.get("scraped_at")),
            "processed_at": datetime.utcnow(),
        }

        return incident

    except Exception as exc:
        print(f"❌ Processing error: {type(exc).__name__}: {str(exc)[:100]}")
        return None
