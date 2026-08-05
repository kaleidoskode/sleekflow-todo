from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env.development  — committed defaults (localhost:5432)
        # .env.local        — gitignored, machine overrides (e.g. DB_PORT=5433)
        env_file=[".env.development", ".env.local"],
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo"
    test_database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo_test"


settings = Settings()
