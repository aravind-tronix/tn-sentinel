# TN Sentinel — Backend

Real-time crime and incident intelligence pipeline for Tamil Nadu. Scrapes Tamil and English news sources, enriches articles through an LLM pipeline, and serves structured incident data via a FastAPI REST API deployed on AWS Lambda.

**Production API:** https://cowriwgugk.execute-api.ap-south-1.amazonaws.com  
**Frontend:** https://tn-intel.in

---

## Architecture

```
Sources (registry.py)
  → scheduler.py — polls each source on its interval
  → RSSExtractor / HTMLExtractor → RawArticle
  → Deduplicator — Redis URL+title hash (48h TTL)
  → RawArticleQueue.push() → Redis list "raw_articles"

queue_worker.py — pops from Redis
  → worker.process_and_save()
      → nlp_preprocessor — spaCy NER, district/category detection
      → triage_chain — YES/NO relevance filter (gemma3:4b)
      → infer_district — Ollama fallback if spaCy misses district
      → extraction_chain — structured JSON (IncidentExtraction schema)
      → embedder — 768-dim nomic-embed-text embedding
      → save_incident() — upsert to PostgreSQL by URL uniqueness
      → publish_event() → POST /broadcast → SSE fan-out
```

---

## Repository layout

```
local_server/           # Local FastAPI app (dev + scraper)
  api/main.py           # FastAPI routes
  config.py             # Pydantic-settings (loaded from config/.env)
  db/models.py          # SQLAlchemy ORM + pgvector
  pipeline/
    llm_chains.py       # Triage, extraction, district LangChain chains
    nlp_preprocessor.py # spaCy NER preprocessing
    worker.py           # process_and_save() orchestrator
    queue_worker.py     # Redis BLPOP consumer daemon
    embedder.py         # nomic-embed-text via Ollama
  scraper/
    registry.py         # Source definitions (RSS + HTML)
    scheduler.py        # Per-source polling loop
    extractors/         # RSSExtractor, HTMLExtractor, BaseExtractor
    dedup.py            # Redis-based URL/title deduplication
    queue.py            # RawArticleQueue push/pop

aws/                    # AWS Lambda deployment
  api_handler.py        # FastAPI + Mangum (same endpoints, DynamoDB backend)
  dynamo.py             # DynamoDB table helpers + item_to_response()

terraform/              # Infrastructure as code (AWS)
  api_gateway.tf        # HTTP API Gateway with CORS
  lambda.tf             # Lambda function + IAM
  dynamodb.tf           # Incidents + WS connections tables
  amplify.tf            # Amplify app + GitHub connection
  variables.tf
  outputs.tf

scripts/
  init_db.py            # Create PostgreSQL schema (run once)
clear_state.py          # Reset Redis queue/dedup or DB (--queue/--dedup/--db/--all)
```

---

## Running locally

Each process runs in a separate terminal:

```bash
# API server
./venv/bin/python -m uvicorn local_server.api.main:app --host 0.0.0.0 --port 8000 --reload

# Scraper scheduler
./venv/bin/python -m local_server.scraper.main

# Queue worker — daemon mode (runs forever)
./venv/bin/python -m local_server.pipeline.queue_worker --pause 0.5

# Queue worker — one-shot (process N items then exit)
./venv/bin/python -m local_server.pipeline.queue_worker --limit 5 --pause 0.5
```

### First-time setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # or install manually — see config.py imports

# Start PostgreSQL and Redis, then:
./venv/bin/python scripts/init_db.py
```

---

## Configuration

All settings load from `config/.env` via `local_server/config.py` (pydantic-settings):

| Variable | Description |
|---|---|
| `DATABASE_URL` | asyncpg PostgreSQL connection string |
| `REDIS_URL` | Redis connection (queue + dedup) |
| `OLLAMA_HOST` | Ollama server URL |
| `OLLAMA_TRIAGE_MODEL` | Model for YES/NO triage (default: `gemma3:4b`) |
| `OLLAMA_EXTRACTOR_MODEL` | Model for JSON extraction (default: `gemma3:4b`) |
| `OLLAMA_EMBED_MODEL` | Embedding model (default: `nomic-embed-text`) |
| `LLM_API_SECRET` | Bearer token for mutating API endpoints (`x-api-key` header) |
| `PIPELINE_DEDUP_THRESHOLD` | Embedding cosine similarity threshold (default: `0.88`) |

---

## API endpoints

### Public

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/incidents` | List incidents — filter by `district`, `category`, `from_date`, `to_date`; paginate with `limit`/`offset` |
| `GET` | `/incidents/{id}` | Single incident |
| `GET` | `/stats/kpis` | 24h KPIs: event count, avg viral score, active sources, high-priority, deltas |
| `GET` | `/stats/districts` | Per-district counts + top category |
| `GET` | `/stats/categories` | Per-category counts |
| `GET` | `/stats/timeline` | Daily counts; `?days=7\|14\|30`; add `&breakdown=true` for per-category rows |
| `GET` | `/stats/sentiment` | Sentiment label distribution |
| `GET` | `/stats/sources` | Top 10 sources by incident count |
| `GET` | `/stats/viral-distribution` | Viral score buckets: Low / Moderate / High / Critical / Viral |
| `GET` | `/stream` | SSE stream of new incidents |

### Protected (`x-api-key` required)

| Method | Path | Description |
|---|---|---|
| `POST` | `/enrich` | Manually submit an article for pipeline processing |
| `POST` | `/broadcast` | Push an event to all SSE listeners |
| `POST` | `/analyze` | Run triage+extraction on raw text |

---

## AWS deployment

Infrastructure is managed with **Terraform** (`terraform/`).

| Resource | Details |
|---|---|
| Lambda | `tn-sentinel-api-prod` (ap-south-1), `aws/api_handler.py` + Mangum |
| API Gateway | HTTP API — `https://cowriwgugk.execute-api.ap-south-1.amazonaws.com` |
| DynamoDB | `tn-sentinel-incidents-prod` — primary store for Lambda API |
| WebSocket API | `wss://0h3s24n0uj.execute-api.ap-south-1.amazonaws.com/prod` |
| Amplify | `d2l0lone63w0d6` — hosts the frontend, connected to GitHub |

### Deploy Lambda changes

```bash
cd aws/
zip -r function.zip api_handler.py dynamo.py
aws lambda update-function-code \
  --function-name tn-sentinel-api-prod \
  --zip-file fileb://function.zip \
  --region ap-south-1
```

### Apply infrastructure changes

```bash
cd terraform/
terraform apply -var="github_token=<token>"
```

---

## Roadmap / TODO

### Source Diversification
> *Feedback: Don't rely solely on large media outlets — editorial priorities influence which stories get covered. Add regional and micro-news sources for broader ground-level coverage.*

- [ ] **Add regional Tamil newspaper RSS feeds** — Dinamalar, Dinakaran, Daily Thanthi, Vikatan, Puthiyathalaimurai, Polimer News, Thina Mani. These cover district-level stories that English mainstream media ignores.
- [ ] **Add hyperlocal sources** — district-level news sites, Tamil Nadu government press releases, district collector announcements.
- [ ] **Integrate NewsAPI / Mediastack** — supports Tamil Nadu geo-filtering; can supplement RSS scraping with broader API coverage.
- [ ] **GDELT live feed** — already used for historical scraping; extend to real-time mode for continuous ingestion.

### Tamil Language Support
- [ ] Add Tamil-language NLP pipeline — current spaCy model is English-only; Tamil sources need a multilingual or Tamil-specific NER model.
- [ ] LLM extraction in Tamil — verify gemma3:4b handles Tamil text or add a translation step before extraction.

---

## Incident schema

| Field | Type | Description |
|---|---|---|
| `title` | string | Extracted article title |
| `district` | string | One of 38 Tamil Nadu districts |
| `category` | string | `Fraud`, `Homicide`, `Assault`, `Cybercrime`, `Road Accident`, `Narcotics`, `Theft`, `Sexual Offence`, `Suicide`, `Infectious Disease`, `Political`, `Other` |
| `viral_score` | 0–100 | Urgency/virality score |
| `sentiment` | string | `positive`, `negative`, `neutral`, `mixed` |
| `summary` | string | LLM-generated 1–2 sentence summary |
| `confidence` | 0–1 | Extraction confidence |
| `published_at` | ISO 8601 | Article publication timestamp |
| `source_name` | string | News outlet name |
| `url` | string | Canonical article URL (unique key) |
