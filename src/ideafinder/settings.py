"""Machine-specific settings, read from environment variables or a local `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDEAFINDER_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    # libpq-style URL. Postgres.app trusts local connections as your macOS user, so no password.
    database_url: str = "postgresql://localhost:5432/ideafinder"
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    config_dir: Path = PROJECT_ROOT / "config"

    # Collection
    user_agent: str = "ideafinder/0.1 (personal research tool)"
    stackexchange_key: SecretStr | None = None  # raises the daily quota from 300 to 10,000

    # Running it daily (M8)
    backup_dir: Path = (
        PROJECT_ROOT / "backups"
    )  # inside your home folder, so Time Machine covers it
    backup_keep_days: int = 14
    pg_bin_dir: Path = Path("/Applications/Postgres.app/Contents/Versions/latest/bin")
    log_dir: Path = PROJECT_ROOT / "logs"
    daily_extract_minutes: int = 90  # model time per daily run; the backlog continues next day

    @property
    def sqlalchemy_url(self) -> str:
        """The same database, addressed through SQLAlchemy's psycopg 3 dialect."""
        scheme, rest = self.database_url.split("://", 1)
        if scheme not in ("postgresql", "postgres"):
            raise ValueError(f"Unsupported database URL scheme: {scheme}")
        return f"postgresql+psycopg://{rest}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
