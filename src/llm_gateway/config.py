from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
