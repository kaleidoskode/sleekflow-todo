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

    # Auth. This default is a development convenience and is deliberately
    # obvious; `require_production_secret()` below refuses to let it through
    # outside a local environment. It is >=32 bytes because HMAC-SHA256 keys
    # shorter than that are weak (RFC 7518 §3.2) and PyJWT warns about them.
    jwt_secret: str = "dev-only-insecure-secret-change-me-in-production"
    access_token_minutes: int = 60 * 12


settings = Settings()

ENVIRONMENT = _ENVIRONMENT
_DEV_ENVIRONMENTS = {"local", "development", "test"}
_DEFAULT_SECRET = Settings.model_fields["jwt_secret"].default


def require_database_url() -> None:
    """Fail with an explanation, not an opaque driver error.

    `.env` is committed and names the environment; the matching `.env.<name>` is
    not always committed — `.env.local` is git-ignored because it holds one
    machine's credentials. So a fresh clone can select an environment whose file
    does not exist, leaving `database_url` empty, and the first symptom is
    SQLAlchemy raising `Could not parse SQLAlchemy URL from string ''` from
    inside an import. That tells a reviewer nothing about what to do next.
    """
    if settings.database_url:
        return
    raise RuntimeError(
        f"No DATABASE_URL. backend/.env selects ENVIRONMENT={ENVIRONMENT!r}, so "
        f"settings are read from backend/.env.{ENVIRONMENT} — which is missing or "
        f"does not set DATABASE_URL.\n"
        f"  For local development:  cp backend/.env.local.example backend/.env.local\n"
        f"  Or run the stack with:  docker compose up -d --build\n"
        f"(Compose passes DATABASE_URL as an environment variable, which takes "
        f"priority over any file.)"
    )


def require_production_secret() -> None:
    """Refuse to boot on the shipped dev secret outside a local environment.

    A signing key committed to the repository means anyone who can read the
    repo can mint valid tokens. Failing loudly at startup is far better than
    discovering it in production.
    """
    if ENVIRONMENT in _DEV_ENVIRONMENTS:
        return
    if settings.jwt_secret == _DEFAULT_SECRET:
        raise RuntimeError(
            f"ENVIRONMENT={ENVIRONMENT!r} is not a development environment, but JWT_SECRET "
            "is still the default committed value. Set JWT_SECRET to a random secret "
            "(e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`)."
        )
    if len(settings.jwt_secret.encode()) < 32:
        raise RuntimeError(
            "JWT_SECRET must be at least 32 bytes for HMAC-SHA256 (RFC 7518 §3.2)."
        )
