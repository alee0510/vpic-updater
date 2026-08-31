"""
Application configuration, loaded from environment variables / .env file.

Two separate DSNs are configured, matching the two-container split agreed
earlier: control-db (vpic_meta -- locking, promotion pointer, audit log)
and target-db (where per-release vpic_{year_month} databases live).
"""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from vpic_updater.core.db import DatabaseDSN


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- control-db (vpic_meta) ---
    control_db_host: str = Field(default="localhost")
    control_db_port: int = Field(default=5433)
    control_db_user: str = Field(default="vpic_admin")
    control_db_password: SecretStr
    control_db_name: str = Field(default="vpic_meta")

    # --- target-db (per-release vpic_{year_month} databases) ---
    target_db_host: str = Field(default="localhost")
    target_db_port: int = Field(default=5434)
    target_db_user: str = Field(default="vpic_admin")
    target_db_password: SecretStr
    target_db_name: str = Field(default="postgres")  # admin/maintenance db

    # --- application role granted access to each new release db ---
    app_role: str = Field(default="vpic_user")

    # --- Slack ---
    slack_webhook_url: str

    # --- local working directories ---
    download_dir: Path = Field(default=Path("data/downloads"))
    extract_dir: Path = Field(default=Path("data/extracted"))
    log_dir: Path = Field(default=Path("logs"))

    @property
    def control_dsn(self) -> DatabaseDSN:
        return DatabaseDSN(
            host=self.control_db_host,
            port=self.control_db_port,
            user=self.control_db_user,
            password=self.control_db_password,
            dbname=self.control_db_name,
        )

    @property
    def target_admin_dsn(self) -> DatabaseDSN:
        return DatabaseDSN(
            host=self.target_db_host,
            port=self.target_db_port,
            user=self.target_db_user,
            password=self.target_db_password,
            dbname=self.target_db_name,
        )


def get_settings() -> Settings:
    """Load settings fresh from environment/.env. Not cached at module
    level -- keeps tests free to monkeypatch env vars per-test without
    worrying about stale cached values."""
    return Settings()