"""The PostgreSQL test harness itself: it must never skip silently in CI, and never wipe a
database that is not a test database. None of these tests need a running PostgreSQL."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from llm_gateway.db.engine import make_engine
from tests.pg import reset_schema

ROOT = Path(__file__).resolve().parents[2]


def _import_harness(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Import ``tests.pg`` in a fresh interpreter, from a folder with no ``.env``."""
    clean = {k: v for k, v in os.environ.items() if not k.upper().startswith("LLMGW_")}
    clean["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-c", "import tests.pg"],
        cwd=tmp_path,
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_required_postgres_without_a_url_fails(tmp_path: Path) -> None:
    result = _import_harness(tmp_path, LLMGW_REQUIRE_POSTGRES="1")
    assert result.returncode != 0
    assert "LLMGW_TEST_DATABASE_URL" in result.stderr


def test_without_the_requirement_a_missing_url_only_skips(tmp_path: Path) -> None:
    assert _import_harness(tmp_path).returncode == 0


def test_the_url_is_read_from_a_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "LLMGW_TEST_DATABASE_URL=postgresql+asyncpg://u:p@localhost/x_test\n", encoding="utf-8"
    )
    assert _import_harness(tmp_path, LLMGW_REQUIRE_POSTGRES="1").returncode == 0


@pytest.mark.parametrize(
    "database", ["llmgw", "llmgw_test_backup", "test", "postgres", "llmgw_TEST", ""]
)
def test_reset_refuses_a_non_test_database(database: str) -> None:
    # Port 1 on localhost: if the guard let it through, this would fail with a
    # connection error instead of ValueError.
    with pytest.raises(ValueError, match="_test"):
        reset_schema(f"postgresql+asyncpg://u:p@127.0.0.1:1/{database}")


@pytest.mark.parametrize("url", ["postgresql://u:p@h/x", "sqlite:///x.db"])
def test_make_engine_refuses_other_drivers(url: str) -> None:
    with pytest.raises(ValueError, match=r"postgresql\+asyncpg://"):
        make_engine(url)
