from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from llm_gateway.db.engine import DRIVER_SCHEME


class Settings(BaseSettings):
    """Runtime configuration, read from ``LLMGW_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="LLMGW_",
        hide_input_in_errors=True,  # a near-miss token must never be echoed into logs
        str_strip_whitespace=True,  # a pasted trailing newline is not part of a secret
        env_ignore_empty=True,  # LLMGW_ANTHROPIC_API_KEY="" means "not configured"
    )

    admin_token: SecretStr = Field(min_length=16)
    default_model: str = "fake-echo"
    anthropic_api_key: SecretStr | None = None
    max_body_bytes: int = Field(1_048_576, ge=1024)  # 1 MiB; bodies above it get 413
    # A provider call that hasn't answered after this many seconds is cut off (504).
    provider_timeout_s: float = Field(120, ge=1, le=600)

    # Unset: everything is stored in memory. Set: PostgreSQL, schema from `alembic upgrade head`.
    database_url: SecretStr | None = None

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url_or_none(cls, url: SecretStr | None) -> SecretStr | None:
        if url is None or not url.get_secret_value():
            return None
        if not url.get_secret_value().startswith(DRIVER_SCHEME):
            raise ValueError(f"the database URL must start with {DRIVER_SCHEME}")
        return url

    @field_validator("anthropic_api_key")
    @classmethod
    def _blank_key_is_no_key(cls, key: SecretStr | None) -> SecretStr | None:
        # env_ignore_empty only catches "", and whitespace is stripped after that check:
        # without this, a key of "   " would enable Claude with an empty credential.
        return key if key is not None and key.get_secret_value() else None
