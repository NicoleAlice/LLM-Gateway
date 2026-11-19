from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from llm_gateway.config import Settings


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    s = Settings(_env_file=None)
    assert s.admin_token.get_secret_value() == "x" * 16
    assert s.default_model == "fake-echo"
    assert s.anthropic_api_key is None


@pytest.mark.parametrize("token", [None, "", "short", " " * 16])
def test_admin_token_required_and_long(monkeypatch: pytest.MonkeyPatch, token: str | None) -> None:
    if token is not None:
        monkeypatch.setenv("LLMGW_ADMIN_TOKEN", token)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_rejected_token_is_not_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "nearly-a-secret")  # 15 chars
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "nearly-a-secret" not in str(excinfo.value)


def test_surrounding_whitespace_is_not_part_of_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "  " + "x" * 16 + "\n")
    assert Settings(_env_file=None).admin_token.get_secret_value() == "x" * 16


@pytest.mark.parametrize("raw", ["", "   ", "\n", "\t \r\n"])
def test_empty_anthropic_key_means_not_configured(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_ANTHROPIC_API_KEY", raw)
    assert Settings(_env_file=None).anthropic_api_key is None


def test_a_real_anthropic_key_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_ANTHROPIC_API_KEY", " sk-ant-abc \n")
    key = Settings(_env_file=None).anthropic_api_key
    assert key is not None and key.get_secret_value() == "sk-ant-abc"


def test_app_uses_injected_clock(client: TestClient, fixed_now: datetime) -> None:
    assert client.app.state.gateway.clock() == fixed_now  # type: ignore[attr-defined]


def test_provider_timeout_defaults_to_120_seconds(settings: Settings) -> None:
    assert settings.provider_timeout_s == 120


@pytest.mark.parametrize(("raw", "seconds"), [("1", 1.0), ("30.5", 30.5), ("600", 600.0)])
def test_provider_timeout_read_from_env(
    monkeypatch: pytest.MonkeyPatch, raw: str, seconds: float
) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_PROVIDER_TIMEOUT_S", raw)
    assert Settings(_env_file=None).provider_timeout_s == seconds


@pytest.mark.parametrize("raw", ["0", "0.99", "600.01", "-5", "nan", "inf", "soon"])
def test_provider_timeout_out_of_bounds_is_rejected(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_PROVIDER_TIMEOUT_S", raw)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


DB_URL = "postgresql+asyncpg://gateway:DB-PASSWORD-MARKER@db.internal:5432/llmgw"


def test_database_url_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    assert Settings(_env_file=None).database_url is None
    monkeypatch.setenv("LLMGW_DATABASE_URL", f" {DB_URL}\n")
    url = Settings(_env_file=None).database_url
    assert url is not None and url.get_secret_value() == DB_URL


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_blank_database_url_means_in_memory(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_DATABASE_URL", raw)
    assert Settings(_env_file=None).database_url is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://gateway:DB-PASSWORD-MARKER@db/llmgw",  # sync driver: not ours
        "postgresql+psycopg://gateway:DB-PASSWORD-MARKER@db/llmgw",
        "sqlite+aiosqlite:///DB-PASSWORD-MARKER.db",
        "DB-PASSWORD-MARKER",
    ],
)
def test_a_database_url_without_asyncpg_is_rejected_without_echo(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_DATABASE_URL", url)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "DB-PASSWORD-MARKER" not in str(excinfo.value)
    assert "postgresql+asyncpg://" in str(excinfo.value)  # says what is expected


def test_the_database_password_is_in_no_repr() -> None:
    settings = Settings(_env_file=None, admin_token="x" * 16, database_url=DB_URL)  # type: ignore[arg-type]
    assert "DB-PASSWORD-MARKER" not in repr(settings)
    assert "DB-PASSWORD-MARKER" not in str(settings)


def test_an_empty_default_model_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_DEFAULT_MODEL", "")
    settings = Settings(_env_file=None)
    assert settings.default_model == "fake-echo"
    assert "default_model" not in settings.model_fields_set
