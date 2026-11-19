"""How ``create_app`` wires the Anthropic provider: only with a key, closed on shutdown,
quiet in the logs. No test here touches the network."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from llm_gateway.billing.pricing import NoPriceError
from llm_gateway.config import Settings
from llm_gateway.main import create_app
from llm_gateway.providers.anthropic import AnthropicProvider
from llm_gateway.state import AppState
from tests.anthropic_mock import answering, client_for, message
from tests.conftest import MutableClock
from tests.helpers import audit_rows, auth, bootstrap, create_key

KEY = "sk-ant-wiring-test-0123456789"
CLAUDE = ("claude-opus-5-5", "claude-sonnet-5-5")
PROMPT = "PROMPT-MARKER-the-user-wrote-this"


def with_key(**extra: object) -> Settings:
    return Settings(_env_file=None, admin_token="test-admin-token", anthropic_api_key=KEY, **extra)  # type: ignore[arg-type]


def gateway(client: TestClient) -> AppState:
    return client.app.state.gateway  # type: ignore[attr-defined, no-any-return]


def test_without_a_key_claude_is_absent(client: TestClient) -> None:
    state = gateway(client)
    assert set(state.providers) == {"fake"}
    assert not set(CLAUDE) & set(state.catalog)
    assert state.settings.default_model == "fake-echo"
    _, admin = bootstrap(client)
    dev = create_key(client, admin, "developer")
    r = client.post(
        "/v1/chat",
        headers=auth(dev),
        json={"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "unknown_model"


def test_with_a_key_claude_is_registered_and_priced(clock: MutableClock) -> None:
    with TestClient(create_app(with_key(), clock=clock)) as client:
        state = gateway(client)
        assert isinstance(state.providers["anthropic"], AnthropicProvider)
        assert state.catalog["fake-echo"] == "fake"  # the fake stays available
        claude_models = {m for m, p in state.catalog.items() if p == "anthropic"}
        assert claude_models == set(CLAUDE)  # no Haiku: its tiered price can't be billed yet
        assert {m: state.catalog[m] for m in CLAUDE} == dict.fromkeys(CLAUDE, "anthropic")
        today = date(2026, 11, 17)
        opus, sonnet = (state.prices.price_for(m, today) for m in CLAUDE)
        assert (opus.input_per_mtok, opus.output_per_mtok) == (Decimal(4), Decimal(20))
        assert (sonnet.input_per_mtok, sonnet.output_per_mtok) == (Decimal(2), Decimal(10))
        assert opus.effective_from == sonnet.effective_from == date(2026, 11, 3)
        with pytest.raises(NoPriceError):
            state.prices.price_for("claude-opus-5-5", date(2026, 11, 2))


def test_with_a_key_opus_becomes_the_default_model(clock: MutableClock) -> None:
    settings = with_key()
    with TestClient(create_app(settings, clock=clock)) as client:
        assert gateway(client).settings.default_model == "claude-opus-5-5"
    assert settings.default_model == "fake-echo"  # a copy is changed, not the caller's object


def test_an_explicit_default_model_is_kept(
    clock: MutableClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLMGW_DEFAULT_MODEL", "fake-echo")
    settings = Settings(_env_file=None, admin_token="test-admin-token", anthropic_api_key=KEY)  # type: ignore[arg-type]
    with TestClient(create_app(settings, clock=clock)) as client:
        assert gateway(client).settings.default_model == "fake-echo"
    assert settings.default_model == "fake-echo"  # the caller's object is not changed


def test_the_built_client_is_configured_and_closed_on_shutdown(clock: MutableClock) -> None:
    app = create_app(with_key(provider_timeout_s=42), clock=clock)
    with TestClient(app) as client:
        provider = gateway(client).providers["anthropic"]
        assert isinstance(provider, AnthropicProvider)
        sdk = provider._client
        assert sdk.max_retries == 2
        assert sdk.timeout == 42
        assert sdk.api_key == KEY
        assert not sdk.is_closed()
    assert sdk.is_closed()


def test_an_injected_client_is_used_and_left_to_its_owner(
    settings: Settings, clock: MutableClock
) -> None:
    # No key in settings: the injected client alone enables Claude (that's how tests run).
    sdk = client_for(answering(200, message()))
    with TestClient(create_app(settings, clock=clock, anthropic_client=sdk)) as client:
        assert gateway(client).settings.default_model == "claude-opus-5-5"
        tenant, admin = bootstrap(client)
        dev = create_key(client, admin, "developer")
        r = client.post(
            "/v1/chat",
            headers=auth(dev),
            json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200, r.text
        # 12 input x $2/MTok + 7 output x $10/MTok
        assert r.json()["cost_usd"] == "0.000094"
        [row] = audit_rows(client, tenant)
        assert (row.provider, row.model, row.status) == ("anthropic", "claude-sonnet-5-5", "ok")
    assert not sdk.is_closed()


def test_sdk_debug_logging_never_shows_the_prompt_or_key(
    settings: Settings, clock: MutableClock, caplog: pytest.LogCaptureFixture
) -> None:
    sdk = client_for(answering(200, message()))
    # The root logger at DEBUG, as a careless deploy might set it.
    app = create_app(settings, clock=clock, anthropic_client=sdk)
    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        _, admin = bootstrap(client)
        dev = create_key(client, admin, "developer")
        r = client.post(
            "/v1/chat",
            headers=auth(dev),
            json={
                "model": "claude-opus-5-5",
                "messages": [{"role": "user", "content": PROMPT}],
            },
        )
        assert r.status_code == 200, r.text
        # Checked while the root logger is still at DEBUG: the pin must hold on its own.
        for name in ("anthropic", "httpx2"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
    assert PROMPT not in caplog.text
    assert "sk-ant-" not in caplog.text


def test_the_key_appears_in_no_repr_or_startup_log(
    clock: MutableClock, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), TestClient(create_app(with_key(), clock=clock)) as client:
        state = gateway(client)
        for text in (repr(state), str(state), repr(state.settings), repr(state.providers)):
            assert KEY not in text
        r = client.get("/health")
        assert KEY not in r.text
    assert KEY not in caplog.text


def test_fixed_now_is_inside_the_claude_price_window(fixed_now: datetime) -> None:
    # Guards the tests above: prices start 2026-11-03, the fixture clock must be after.
    assert fixed_now >= datetime(2026, 11, 3, tzinfo=UTC)


def test_an_injected_client_wins_over_the_key(clock: MutableClock) -> None:
    # A test that injects a mock must never get a real network client, key or not.
    sdk = client_for(answering(200, message()))
    with TestClient(create_app(with_key(), clock=clock, anthropic_client=sdk)) as client:
        provider = gateway(client).providers["anthropic"]
        assert isinstance(provider, AnthropicProvider)
        assert provider._client is sdk
    assert not sdk.is_closed()


def test_a_blank_key_leaves_claude_off(clock: MutableClock) -> None:
    settings = Settings(_env_file=None, admin_token="test-admin-token", anthropic_api_key="   ")  # type: ignore[arg-type]
    with TestClient(create_app(settings, clock=clock)) as client:
        assert "anthropic" not in gateway(client).providers
        assert gateway(client).settings.default_model == "fake-echo"


def test_an_ambient_base_url_is_ignored(
    clock: MutableClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://elsewhere.example")
    with TestClient(create_app(with_key(), clock=clock)) as client:
        provider = gateway(client).providers["anthropic"]
        assert isinstance(provider, AnthropicProvider)
        assert str(provider._client.base_url).rstrip("/") == "https://api.anthropic.com"


def test_fake_echo_still_answers_with_a_key(clock: MutableClock) -> None:
    with TestClient(create_app(with_key(), clock=clock)) as client:
        _, admin = bootstrap(client)
        dev = create_key(client, admin, "developer")
        r = client.post(
            "/v1/chat",
            headers=auth(dev),
            json={"model": "fake-echo", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200 and r.json()["content"] == "echo: hi"


async def test_the_built_client_is_closed_even_if_the_app_dies(clock: MutableClock) -> None:
    # A crash while serving reaches the lifespan as an exception; the client still closes.
    app = create_app(with_key(), clock=clock)
    provider = app.state.gateway.providers["anthropic"]
    assert isinstance(provider, AnthropicProvider)
    with pytest.raises(RuntimeError, match="server died"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("server died")
    assert provider._client.is_closed()
