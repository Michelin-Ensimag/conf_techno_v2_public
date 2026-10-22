"""Environment-based settings of the player."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Identity: obtained once from the admin-panel registration page.
    team_username: str = ""
    team_private_key: str = ""
    team_public_key: str = ""
    # Public key of the admin identity, used to verify every message received.
    admin_public_key: str = ""
    # Name of the admin identity (admin-panel's ADMIN_USERNAME).
    admin_username: str = "admin"

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "client-student"
    game_commands_topic: str = "game.commands"
    game_facts_topic: str = "game.facts"

    log_level: str = "info"
    # How long to wait for the admin-panel's answer to the startup ping.
    ping_timeout_seconds: float = 10.0

    @field_validator("team_private_key", "team_public_key", "admin_public_key")
    @classmethod
    def _expand_escaped_newlines(cls, value: str) -> str:
        # PEM keys fit on one line in an env file when newlines are written as "\n".
        return value.replace("\\n", "\n").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
