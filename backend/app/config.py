from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/promise_tracker"

    # LLM provider: "ollama" (local/free) or "anthropic" (production)
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Scraper
    scraper_request_timeout: int = 20
    scraper_max_retries: int = 3
    prs_india_base_url: str = "https://prsindia.org"
    sansad_base_url: str = "https://sansad.in"
    pib_base_url: str = "https://pib.gov.in"
    news_api_key: str = ""

    # Embedding (near-duplicate detection)
    embedding_model: str = "nomic-embed-text"   # free, local via Ollama
    embedding_similarity_threshold: float = 0.85
    embedding_candidate_limit: int = 500        # max existing promises to compare against

    # Pipeline tuning
    min_confidence_score: float = 0.65      # discard extractions below this
    contradiction_batch_size: int = 20      # max existing promises per contradiction LLM call
    status_check_batch_size: int = 10       # max promises per status-check LLM call

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_cron_hour: int = 2            # 2 AM IST nightly
    scheduler_timezone: str = "Asia/Kolkata"

    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
