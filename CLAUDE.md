# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the System

All commands use the project virtualenv. Run each process in a separate terminal:

```bash
# API server
./venv/bin/python -m uvicorn local_server.api.main:app --host 0.0.0.0 --port 8000 --reload

# Scraper scheduler (initial pass + loop)
./venv/bin/python -m local_server.scraper.main

# Queue worker (daemon — runs forever, wakes on new items)
./venv/bin/python -m local_server.pipeline.queue_worker --pause 0.5

# Queue worker (one-shot — process N items then exit)
./venv/bin/python -m local_server.pipeline.queue_worker --limit 5 --pause 0.5
```

## Utility Scripts

```bash
# Initialize DB schema (run once; requires pgvector extension in Postgres)
./venv/bin/python scripts/init_db.py

# Reset state (use flags: --queue, --dedup, --db, or --all)
./venv/bin/python clear_state.py --all

# Manually process a single article JSON file through the pipeline
./venv/bin/python -m local_server.pipeline.worker sample_article.json
```

## Configuration

All settings are loaded from `config/.env` via `local_server/config.py` (pydantic-settings). Key variables:

- `DATABASE_URL` — asyncpg PostgreSQL connection string
- `REDIS_URL` — Redis connection (queue + dedup)
- `OLLAMA_HOST`, `OLLAMA_TRIAGE_MODEL`, `OLLAMA_EXTRACTOR_MODEL`, `OLLAMA_EMBED_MODEL` — Ollama models
- `LLM_API_SECRET` — bearer token required on all mutating API endpoints via `x-api-key` header
- `PIPELINE_DEDUP_THRESHOLD` — cosine similarity threshold (default 0.88) for embedding-based dedup

## Architecture

### Data Flow

```
Sources (registry.py)
  → scheduler.py runs each source on its interval
  → RSSExtractor / HTMLExtractor produce RawArticle objects
  → Deduplicator checks Redis (URL hash + title hash, 48h TTL)
  → RawArticleQueue.push() → Redis list "raw_articles"

queue_worker.py pops from Redis
  → worker.process_and_save()
      → nlp_preprocessor.preprocess() — spaCy NER, district/category detection
      → llm_chains.process_article()
          1. triage_chain: YES/NO relevance filter (gemma3:4b, 800-char input)
          2. infer_district: Ollama fallback if spaCy district detection failed
          3. extraction_chain: structured JSON extraction (IncidentExtraction schema)
          4. embedder.aembed_query(): 768-dim nomic-embed-text embedding
      → save_incident() — upsert to PostgreSQL by URL uniqueness
      → publish_event() — HTTP POST to /broadcast → EventBroadcaster fans out to SSE listeners
```

### LLM Pipeline Details (`pipeline/llm_chains.py`)

Three LangChain chains share the same Ollama models:
- `triage_chain` — `TRIAGE_PROMPT | triage_model` — fast YES/NO, 1024-token context
- `extraction_chain` — `EXTRACT_PROMPT | extractor_model | PydanticOutputParser` — produces `IncidentExtraction`
- `district_chain` — `DISTRICT_PROMPT | extractor_model` — used only when spaCy fails to detect district

The `IncidentExtraction` schema drives what gets persisted: `title`, `district`, `category` (one of 8 values), `viral_score` (0–100), `summary`, `sentiment`, `confidence`.

spaCy preprocessing runs first and supplies NLP hints (detected district, category, entities) into the extraction prompt, reducing LLM errors.

### Scraper Extractors (`scraper/extractors/`)

- `BaseExtractor` — abstract base, `RawArticle` dataclass, `parse_date()`, `normalize_text()`
- `RSSExtractor` — fetches feed, decodes Google News `/articles/` base64 URLs, resolves redirects, then fetches full article text
- `HTMLExtractor` — scrapes listing pages using per-source CSS selectors from `registry.py`, then fetches full article text per item

Both extractors fall back through a hardcoded `ARTICLE_SELECTORS` list and a longest-text heuristic when source-specific selectors fail.

### API (`api/main.py`)

Protected endpoints (require `x-api-key` header): `POST /enrich`, `POST /broadcast`, `POST /analyze`

Public endpoints: `GET /incidents`, `GET /incidents/{id}`, `GET /stats/kpis`, `GET /stats/districts`, `GET /stats/categories`, `GET /stream` (SSE)

`EventBroadcaster` is a simple in-process asyncio.Queue fan-out — it does not persist events. SSE clients that connect late miss earlier events.

### Adding a New Source

Add an entry to `SOURCES` in `scraper/registry.py`. Fields:
- `type`: `"rss"` or `"html"`
- `interval_min`: polling interval
- `selectors` (HTML only): `articles`, `title`, `link`, `summary`, `time`, `image` CSS selectors
- For RSS with custom article content selectors, add `selectors.content`
