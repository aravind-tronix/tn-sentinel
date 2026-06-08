**Project Overview**

This repository implements a Tamil Nadu incident collection and enrichment system. It combines deterministic scraping (RSS, site-specific HTML extraction) with LLM-assisted enrichment (triage, structured extraction, embedding generation) and stores results in PostgreSQL with vector embeddings for semantic search.

**Primary Goals**
- **Discover** candidate incident articles from feeds and web pages.
- **Filter & Deduplicate** incoming items to avoid duplicate or low-quality content.
- **Enrich** articles using LLMs to extract structured metadata (title, summary, category, entities, published_at, confidence).
- **Persist** canonical incidents and scrape logs in Postgres and expose them via a FastAPI HTTP API.
- **Scale** via background workers and queues for LLM calls.

**Recent Scraper Fixes**
- Google News RSS URLs are now resolved to actual publisher URLs before fetching article text, using direct URL decoding and HTTP redirects.
- The Hindu source extraction now includes content selectors for `div.article-text`, `div.storyline`, `div.article__content`, and `section.article-content`.
- Polluted nav/sidebar content such as "Trending on The Hindu" is detected and the extractor falls back to RSS summary when needed.

**Repository Layout (high level)**n- **local_server/**: main application package
  - [local_server/config.py](local_server/config.py): configuration and environment settings
  - [local_server/api/](local_server/api): FastAPI endpoints and broadcaster
  - [local_server/db/models.py](local_server/db/models.py): SQLAlchemy ORM, engine and session factory
  - [local_server/pipeline/](local_server/pipeline): LLM clients, preprocessing, worker orchestration
  - [local_server/scraper/](local_server/scraper): scrapers, extractors, dedup logic and scheduler

- **scripts/**: convenience scripts
  - `init_db.py`: create DB tables using SQLAlchemy models
  - (historical) clear_db.py may not be present — ad-hoc SQL may be used instead

**End-to-End Data Flow**
1. Sources & Discovery
   - `local_server/scraper/registry.py` defines `SOURCES` (RSS feeds, site configs).
   - The scheduler (`local_server/scraper/scheduler.py`) runs each source on its interval producing candidate article objects.

2. Deduplication & Queueing
   - Deduplication is handled by `local_server/scraper/dedup.py` using Redis (or in-memory TTL keys) to avoid reprocessing the same URL/title.
   - New articles are pushed to a raw queue (`local_server/scraper/queue.py` / `RawArticleQueue`) for downstream processing.

3. Enrichment & Classification
   - The pipeline components in `local_server/pipeline/` accept raw article payloads and perform:
     - text cleaning and pre-processing: `nlp_preprocessor.py`
     - triage classification (is this relevant?) using a lightweight LLM call
     - structured detail extraction via the production LLM client (Grok/Ollama wrappers)
     - embedding generation (via Ollama / embedding provider) for semantic dedupe/search

   - LLM calls are implemented with robust parsing: request `text.format.type=json_object` when possible and run JSON extraction + validation to handle noisy model outputs.

4. Saving to Database
   - `local_server/db/models.py` defines `Incident` and `ScrapeLog` tables. Key fields include `url`, `title`, `summary`, `entities`, `published_at`, and `embedding` (pgvector).
   - Save logic ensures unique `url` constraint and additional near-duplicate checks (embedding similarity threshold).

5. API & UI
   - The FastAPI app in `local_server/api` exposes endpoints to list incidents, get details, and stream new incidents via a broadcaster (WebSocket/Server-Sent Events).
   - A simple React UI can be mounted in `ui/` (not present) to consume these endpoints.

**Key Files and Entry Points**
- App configuration: [local_server/config.py](local_server/config.py)
- DB models & init: [local_server/db/models.py](local_server/db/models.py) and `scripts/init_db.py`
- Scraper scheduler & runner: [local_server/scraper/main.py](local_server/scraper/main.py) and [local_server/scraper/scheduler.py](local_server/scraper/scheduler.py)
- LLM pipeline: `local_server/pipeline/` (see `nlp_preprocessor.py`, `worker.py`, `llm_chains.py`)
- API: `local_server/api/main.py` (FastAPI app)

**Configuration & Environment**
- Defaults are loaded from `config/.env` (path defined in `local_server/config.py`). The `Settings` class exposes defaults and expected keys.
- Important settings:
  - `DATABASE_URL` (e.g., `postgresql+asyncpg://user:pass@localhost:5432/dbname`)
  - `REDIS_URL` (for dedup/queue)
  - `LLM_API_SECRET` (for remote LLM providers if used)
  - `OLLAMA_HOST` and Ollama model settings (if using Ollama)
   - `OLLAMA_SINGLE_MODEL` (optional): model name for advanced experiments (e.g., `qwen3:7b`).

**Local Development Quickstart**
1. Create virtualenv and install dependencies (example):
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
# install runtime deps (adjust as necessary)
pip install fastapi uvicorn sqlalchemy asyncpg pydantic pydantic-settings requests pgvector python-dotenv
```

2. Add `config/.env` (see `local_server/config.py` for keys).

3. Ensure PostgreSQL and Redis are running and reachable from `DATABASE_URL` and `REDIS_URL`.

   Example Redis startup commands:
   ```bash
   # systemd-managed Redis
   sudo systemctl start redis
   sudo systemctl status redis

   # or start a local Redis server directly
   redis-server --daemonize yes
   
   # or use Docker
   docker run -d --name tn-redis -p 6379:6379 redis:7
   ```

4. Initialize DB schema:
```bash
./venv/bin/python scripts/init_db.py
```

5. Start the API (development):
```bash
./venv/bin/python -m uvicorn local_server.api.main:app --host 0.0.0.0 --port 8000 --reload
```

6. Start the scraper (initial pass + scheduler loop):
```bash
./venv/bin/python -m local_server.scraper.main
```

7. Start the queue worker to process incoming articles:
```bash
./venv/bin/python -m local_server.pipeline.queue_worker --limit 2 --pause 1
```

### Start everything together
You can start the API, scraper, and queue worker in separate terminals, or use a terminal multiplexer such as `tmux`/`screen` to run all three processes at once. A process manager such as `systemd`, `supervisord`, or `docker-compose` is more robust for long-lived services and can start all components together automatically.

### Keep queue workers running automatically
The worker should ideally run as a long-lived daemon that polls Redis continuously for new `raw_articles`. If the worker command is limited by `--limit`, it may exit after processing a fixed number of items, so use a supervisor or restart policy to keep it running.

- Run at least one queue worker continuously
- If load grows, run multiple workers in parallel
- Use a supervisor to restart workers automatically if they crash

This keeps the queue processing active as new articles arrive.

**LLM Integration Notes**
- Prefer deterministic discovery (RSS) and simple scrapers for recall; use LLMs primarily for structured extraction and classification.
- Request JSON-structured responses where possible. Always validate and sanitize model output before persisting.
- Keep `temperature` low for deterministic outputs and add retry/prompt-simplification on failure.

**Ollama Models and NLP Rationale**
- `gemma3:4b` is used for both triage and structured extraction. It is lighter and faster, and it handles relevance prediction plus JSON extraction for incident fields.
- `nomic-embed-text` is used for embeddings only. It converts the extracted incident text into a vector for semantic deduplication and similarity.
- NLP with spaCy is used before Ollama. The pipeline loads `en_core_web_sm` for English and `xx_ent_wiki_sm` for Tamil.
- The spaCy preprocessing step extracts entities, detects districts and crime categories from text, and supplies hints to the LLM, which reduces LLM cost and improves extraction accuracy.

**Queue Processing Details**
- The Redis queue key is `raw_articles`.
- The queue consumer is `local_server/pipeline/queue_worker.py`.
- To process queued articles manually:
```bash
cd /home/aravind/tn-intel
./venv/bin/python -m local_server.pipeline.queue_worker --limit 5 --pause 0.5
```
- The worker pops items from Redis, processes each through the LLM pipeline, and saves results to PostgreSQL.

**What happens after queue processing**
- Each queued raw article is removed from Redis once popped.
- The raw article is passed to `local_server/pipeline/worker.process_and_save()`.
- If the article is filtered as not relevant or invalid, it is discarded and not saved.
- If the article is accepted, it is converted into an `Incident` record and inserted into the `incidents` table.
- Before inserting, the worker checks for an existing incident with the same `url`; duplicates are not re-saved.
- There is no automatic requeue of processed items in the current design.

**Deduplication Strategy**
- URL normalization + canonical host/path stripping
- Exact URL uniqueness via DB `unique` constraint
- Content-level near-duplicate detection using embeddings and a similarity threshold (`pipeline_dedup_threshold` in `local_server/config.py`)
- Optional fuzzy hashing (SimHash) for robust duplicate detection across small edits

**Persistence & Indexing**
- Use `pgvector` extension to store embeddings. Create the extension in your Postgres DB before running init.
- Add necessary indexes (see `__table_args__` in [local_server/db/models.py](local_server/db/models.py)).

**Scaling & Production Recommendations**
- Move LLM calls to background workers with a persistent queue (Redis + RQ/Celery) for reliability and observability.
- Add Alembic for schema migrations instead of direct `create_all` in production.
- Containerize components (API, worker, redis, postgres) and orchestrate with docker-compose or Kubernetes.
- Add monitoring (Prometheus/Grafana) and structured logging for pipeline observability.

**Testing & CI**
- Unit tests for extractors in `local_server/scraper/extractors`.
- Integration tests that run `init_db.py` against a test Postgres instance and exercise the full pipeline with mocked LLM responses.

**Troubleshooting**
- If LLM outputs fail to parse: lower `temperature`, simplify prompts, log raw outputs, and implement fallback deterministic parsers.
- If embeddings fail: verify `pgvector` extension is installed and the DB column type matches the configured embedding dimension.

**Next Steps / Roadmap**
- Add Alembic migrations and a `requirements.txt` or `pyproject.toml`.
- Implement background worker queue for LLM jobs.
- Scaffold a small React UI in `ui/` to view live incidents.
- Harden dedup pipeline with multi-step similarity checks.
**AWS Migration Plan**
- Migrate REST API endpoints to AWS Lambda and API Gateway.
- Migrate persistent incident storage from PostgreSQL to DynamoDB.
- Replace the local in-memory SSE broadcaster with API Gateway WebSockets and DynamoDB-backed connection management.
- Keep scraper/pipeline on Ubuntu and have the worker publish new incident events to AWS for real-time delivery.
- See `LAMBDA_MIGRATION_CHECKLIST.md` for the full migration workflow and implementation steps.
---
This README describes architecture and developer steps to run the system end-to-end. If you want, I can also:
- create `config/.env.sample`,
- generate `requirements.txt`, or
- scaffold a `ui/` React app and basic Docker compose. Tell me which one to do next.
