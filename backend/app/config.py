from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/promise_tracker"

    # LLM provider: "ollama" (local/free) or "anthropic" (production)
    llm_provider: str = "ollama"

    # Ollama settings (local Llama)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"

    # Anthropic settings (upgrade path)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    news_api_key: str = ""
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
