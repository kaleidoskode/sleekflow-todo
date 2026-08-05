import os

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ENV = os.getenv("APP_ENV", "local")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=f".env.{APP_ENV}",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo"
    test_database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo_test"


settings = Settings()
