from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://expense:expense@localhost:5433/expense_tracker"
    cors_origins: list[str] = ["http://localhost:5173"]

    jwt_secret_key: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    redis_url: str = "redis://localhost:6379/0"

    openai_api_key: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()
