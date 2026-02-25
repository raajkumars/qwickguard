from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_path: str = "/data/qwickguard.db"
    anthropic_api_key: str | None = None
    github_token: str | None = None
    github_repo: str = "raajkumars/qwickguard"
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    heartbeat_timeout_minutes: int = 15
    max_claude_calls_per_day: int = 20
    data_retention_days: int = 7

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
