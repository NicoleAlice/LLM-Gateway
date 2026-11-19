"""One real call to the Anthropic API, through the whole gateway.

This costs real money (well under a tenth of a cent per run), so it never runs by
default and never in CI. It runs only with the ``live`` marker AND both variables set:

    LLMGW_LIVE_TESTS=1
    LLMGW_ANTHROPIC_API_KEY=sk-ant-...   (in the shell or in ./.env)

    uv run pytest -m live
"""

import os
from decimal import Decimal
from pathlib import Path

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from llm_gateway.config import Settings
from llm_gateway.main import create_app
from tests.helpers import ADMIN_TOKEN, audit_rows, auth, bootstrap, create_key

KEY_VAR = "LLMGW_ANTHROPIC_API_KEY"
MODEL = "claude-opus-5-5"
MAX_TOKENS = 32
INPUT_PER_MTOK, OUTPUT_PER_MTOK = 4, 20  # claude-opus-5-5, from 2026-11-03


def _live_key() -> str | None:
    """The key, read once at import: the autouse ``_clean_env`` fixture strips LLMGW_*
    from the environment before each test runs."""
    if os.environ.get("LLMGW_LIVE_TESTS") != "1":
        return None
    env_file = Path(".env")
    from_file = dotenv_values(env_file).get(KEY_VAR) if env_file.is_file() else None
    key = (os.environ.get(KEY_VAR) or from_file or "").strip()
    return key or None


LIVE_KEY = _live_key()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(LIVE_KEY is None, reason=f"live test: set LLMGW_LIVE_TESTS=1 and {KEY_VAR}"),
]


def test_a_real_claude_call_is_answered_and_billed() -> None:
    assert LIVE_KEY is not None
    settings = Settings(_env_file=None, admin_token=ADMIN_TOKEN, anthropic_api_key=LIVE_KEY)  # type: ignore[arg-type]
    with TestClient(create_app(settings)) as client:
        tenant, admin = bootstrap(client)
        dev = create_key(client, admin, "developer")
        r = client.post(
            "/v1/chat",
            headers=auth(dev),
            json={
                "model": MODEL,
                "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["provider"], body["model"]) == ("anthropic", MODEL)
        assert body["content"].strip()
        assert body["stop_reason"] in {"end_turn", "max_tokens"}

        used_in, used_out = body["usage"]["input_tokens"], body["usage"]["output_tokens"]
        assert used_in > 0 and 0 < used_out <= MAX_TOKENS
        expected = Decimal(used_in * INPUT_PER_MTOK + used_out * OUTPUT_PER_MTOK) / 10**6
        assert Decimal(body["cost_usd"]) == expected > 0

        [row] = audit_rows(client, tenant)
        assert (row.provider, row.model, row.status) == ("anthropic", MODEL, "ok")
        assert (row.input_tokens, row.output_tokens) == (used_in, used_out)
        assert row.cost_usd == expected
    assert LIVE_KEY not in r.text
