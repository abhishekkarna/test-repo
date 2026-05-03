from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/promise_tracker"
    anthropic_api_key: str = ""
    news_api_key: str = ""
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
