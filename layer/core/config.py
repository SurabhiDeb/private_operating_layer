"""Settings for the Layer.

Separate from EarlyEcho's `core/config.py` on purpose: different product, different
database, different required variables. Every name is prefixed `LAYER_` so the two
can share a `.env` without colliding.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LAYER_", extra="ignore"
    )

    # The application role. Must NOT own the tables, so that row level security
    # applies to it. See layer/db/alembic/versions for the role grants.
    database_url: str

    # A superuser or table-owner URL, used only by migrations and by the test
    # harness when it needs to create or drop schema.
    admin_database_url: str = ""

    # The model used by the spec extractor's second pass. Any OpenAI-compatible
    # endpoint. Absent means the deterministic structural pass runs alone.
    ai_api_key: str = ""
    ai_base_url: str = "https://api.anthropic.com/v1/"
    ai_model: str = "claude-opus-5"

    # Default revision to pin citations to when a source names none. Empty means
    # the adapter must resolve one; a branch name is never acceptable (AC-14).
    default_pinned_rev: str = ""

    log_level: str = "INFO"

    @property
    def migration_url(self) -> str:
        return self.admin_database_url or self.database_url


settings = Settings()  # type: ignore[call-arg]
