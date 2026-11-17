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


def test_empty_anthropic_key_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_ADMIN_TOKEN", "x" * 16)
    monkeypatch.setenv("LLMGW_ANTHROPIC_API_KEY", "")
    assert Settings(_env_file=None).anthropic_api_key is None


def test_app_uses_injected_clock(client: TestClient, fixed_now: datetime) -> None:
    assert client.app.state.gateway.clock() == fixed_now  # type: ignore[attr-defined]
