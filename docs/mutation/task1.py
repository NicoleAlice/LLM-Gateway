"""Mutation run for LLM-Gateway Task 1. Each mutant: apply, run the check, restore."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
SRC = ROOT / "src" / "llm_gateway"

MUTANTS = [
    # (id, file, old, new, check)
    ("T1-01 min_length removed", SRC / "config.py", "Field(min_length=16)", "Field()", "pytest"),
    ("T1-02 default model changed", SRC / "config.py", '"fake-echo"', '"fake"', "pytest"),
    ("T1-03 env prefix removed", SRC / "config.py", 'env_prefix="LLMGW_"', 'env_prefix=""', "pytest"),
    ("T1-04 anthropic key defaults to value", SRC / "config.py", "SecretStr | None = None", 'SecretStr | None = SecretStr("x")', "pytest"),
    ("T1-05 injected clock ignored", SRC / "main.py", "clock=clock if clock is not None else _utc_now", "clock=_utc_now", "pytest"),
    ("T1-06 injected settings ignored", SRC / "main.py", "    settings = settings if settings is not None else Settings()\n", "    settings = Settings()\n", "pytest"),
    ("T1-07 module-level app re-added", SRC / "main.py", "    return app\n", "    return app\n\n\napp = create_app()\n", "pytest"),
    ("T1-08 health status wrong", SRC / "main.py", '{"status": "ok"', '{"status": "up"', "pytest"),
    ("T1-12 input shown in errors", SRC / "config.py", "hide_input_in_errors=True", "hide_input_in_errors=False", "pytest"),
    ("T1-13 whitespace not stripped", SRC / "config.py", "str_strip_whitespace=True", "str_strip_whitespace=False", "pytest"),
    ("T1-14 empty env treated as value", SRC / "config.py", "env_ignore_empty=True", "env_ignore_empty=False", "pytest"),
    ("T1-09 mypy pydantic plugin removed", ROOT / "pyproject.toml", 'plugins = ["pydantic.mypy"]', "", "mypy"),
    ("T1-10 test S105/S106 ignore removed", ROOT / "pyproject.toml", '"tests/**" = ["S105", "S106"]', "", "ruff"),
    ("T1-11 docs exclude removed", ROOT / "pyproject.toml", 'extend-exclude = ["docs"]', "", "format"),
]

CHECKS = {
    "pytest": [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings"],
    "mypy": [sys.executable, "-m", "mypy"],
    "ruff": [sys.executable, "-m", "ruff", "check", "."],
    "format": [sys.executable, "-m", "ruff", "format", "--check", "."],
}


def run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, env=env).returncode


results = []
for mid, path, old, new, check in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = run(CHECKS[check])
    finally:
        path.write_text(original, encoding="utf-8")
    results.append((mid, check, "killed" if rc != 0 else "SURVIVED"))

# Isolation check: with LLMGW_* set in the shell, the suite must still pass (autouse _clean_env).
env = dict(os.environ, LLMGW_ADMIN_TOKEN="from-the-shell-123456", LLMGW_DEFAULT_MODEL="leaked",
           LLMGW_ANTHROPIC_API_KEY="leaked-key", LLMGW_FUTURE_SETTING="x")
results.append(("ENV leak guard (should PASS)", "pytest", "pass" if run(CHECKS["pytest"], env) == 0 else "FAIL"))

for row in results:
    print("\t".join(row))
print("baseline after restore:", "green" if run(CHECKS["pytest"]) == 0 else "RED")
