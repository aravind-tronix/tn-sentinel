from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / "config" / ".env"

class Settings(BaseSettings):
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_env: str = "development"
    debug: bool = True

    llm_api_secret: str = "changethis123"

    database_url: str = "postgresql+asyncpg://tn_intel_user:tn_intel_pass@localhost:5432/tn_intel"

    redis_url: str = "redis://localhost:6379"
    redis_raw_queue: str = "raw_articles"
    redis_dedup_ttl: int = 172800

    ollama_host: str = "http://localhost:11434"
    ollama_triage_model: str = "gemma3:4b"
    ollama_extractor_model: str = "gemma3:4b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_single_model: str | None = None
    ollama_num_ctx: int = 4096
    ollama_num_gpu: int = 1
    ollama_num_thread: int = 6
    ollama_temperature: float = 0.0

    event_broadcaster_url: str = "http://localhost:8000"

    pipeline_dedup_threshold: float = 0.88
    pipeline_max_text_length: int = 3500

    cors_allowed_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,https://tn-intel.in,https://www.tn-intel.in,https://main.d2l0lone63w0d6.amplifyapp.com,https://d2l0lone63w0d6.amplifyapp.com"

    # NewsAPI — leave blank to disable (get key at newsapi.org)
    newsapi_key: str = ""

    # AWS / DynamoDB — leave blank to disable cloud writes
    aws_region: str = "ap-south-1"
    dynamodb_incidents_table: str = ""
    dynamodb_ws_connections_table: str = ""
    aws_ws_endpoint: str = ""  # https://xxx.execute-api.region.amazonaws.com/prod

    model_config = {
        "env_file": str(ENV_FILE),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

@lru_cache()
def get_settings() -> Settings:
    return Settings()
