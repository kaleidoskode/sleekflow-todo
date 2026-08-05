from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Read ENVIRONMENT from the committed .env (e.g. ENVIRONMENT=local).
# This is the switch — change it there, not here.
_ENV_FILE = Path(".env")
_ENVIRONMENT = "local"
if _ENV_FILE.is_file():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line.startswith("ENVIRONMENT="):
            _ENVIRONMENT = _line.split("=", 1)[1].strip().strip('"').strip("'")
            break


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=f".env.{_ENVIRONMENT}",
        extra="ignore",
    )

    database_url: str = ""
    test_database_url: str = ""


settings = Settings()
