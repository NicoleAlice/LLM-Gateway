"""Mutation run for plan 1b-B task B3 (wiring the Anthropic provider into the app).

Apply, run pytest, restore. pytest runs with this interpreter (``sys.executable``), not
``uv run``, so an environment sync failure can't be mistaken for a killed mutant."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
MAIN, STATE, CFG = S / "main.py", S / "state.py", S / "config.py"

MUTANTS = [
    ('W01 key ignored', MAIN, '    if anthropic_client is None and settings.anthropic_api_key is not None:\n', '    if False:\n'),
    ('W02 client built without a key', MAIN, '    if anthropic_client is None and settings.anthropic_api_key is not None:\n', '    if anthropic_client is None:\n'),
    ('W03 no retries', MAIN, 'ANTHROPIC_MAX_RETRIES = 2\n', 'ANTHROPIC_MAX_RETRIES = 0\n'),
    ('W04 SDK timeout not the setting', MAIN, '            timeout=settings.provider_timeout_s,\n', '            timeout=600,\n'),
    ('W05 built client never closed', MAIN, '                await owned.close()\n', '                pass\n'),
    ('W06 injected client closed too', MAIN, '            if owned is not None:\n', '            if anthropic_client is not None:\n'),
    ('W07 opus never the default', MAIN, 'if anthropic_client is not None and "default_model" not in settings.model_fields_set:', 'if False:'),
    ('W08 explicit default overridden', MAIN, 'if anthropic_client is not None and "default_model" not in settings.model_fields_set:', 'if anthropic_client is not None:'),
    ("W09 caller's settings mutated", MAIN, '        settings = settings.model_copy(update={"default_model": DEFAULT_CLAUDE_MODEL})\n', '        settings.default_model = DEFAULT_CLAUDE_MODEL\n'),
    ('W10 injected client ignored', MAIN, '    if anthropic_client is not None:\n        add_anthropic(state, anthropic_client)\n', '    if False:\n        add_anthropic(state, anthropic_client)\n'),
    ('W11 SDK loggers not pinned', MAIN, '        logging.getLogger(name).setLevel(logging.WARNING)\n', '        logging.getLogger(name)\n'),
    ('W12 httpx2 logger not pinned', MAIN, '_QUIET_LOGGERS = ("anthropic", "httpx2")\n', '_QUIET_LOGGERS = ("anthropic",)\n'),
    ('W13 loggers pinned at DEBUG', MAIN, '        logging.getLogger(name).setLevel(logging.WARNING)\n', '        logging.getLogger(name).setLevel(logging.DEBUG)\n'),
    ('W14 key not passed', MAIN, '            api_key=settings.anthropic_api_key.get_secret_value(),\n', '            api_key="unset",\n'),
    ('S01 opus input price', STATE, 'Price("claude-opus-5-5", date(2026, 11, 3), Decimal("4"), Decimal("20"))', 'Price("claude-opus-5-5", date(2026, 11, 3), Decimal("5"), Decimal("20"))'),
    ('S02 opus output price', STATE, 'Price("claude-opus-5-5", date(2026, 11, 3), Decimal("4"), Decimal("20"))', 'Price("claude-opus-5-5", date(2026, 11, 3), Decimal("4"), Decimal("25"))'),
    ('S03 sonnet input price', STATE, 'Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10"))', 'Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("3"), Decimal("10"))'),
    ('S04 sonnet output price', STATE, 'Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10"))', 'Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("15"))'),
    ('S05 opus price starts too early', STATE, 'Price("claude-opus-5-5", date(2026, 11, 3)', 'Price("claude-opus-5-5", date(2026, 11, 1)'),
    ('S06 sonnet price starts too late', STATE, 'Price("claude-sonnet-5-5", date(2026, 11, 3)', 'Price("claude-sonnet-5-5", date(2026, 11, 18)'),
    ('S07 models routed to the fake', STATE, '        state.catalog[price.model] = "anthropic"\n', '        state.catalog[price.model] = "fake"\n'),
    ('S08 prices not registered', STATE, '        state.prices.add(price)\n', '        pass\n'),
    ('S09 provider not registered', STATE, '    state.providers["anthropic"] = AnthropicProvider(client)\n', '    pass\n'),
    ('S10 wrong default model', STATE, 'DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"\n', 'DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"\n'),
    ('S11 sonnet left out', STATE, '    Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10")),\n', ''),
    ('W15 key client overrides the injected one', MAIN, '    if anthropic_client is None and settings.anthropic_api_key is not None:\n', '    if settings.anthropic_api_key is not None:\n'),
    ('W16 opus default only with a key', MAIN, 'if anthropic_client is not None and "default_model" not in settings.model_fields_set:', 'if settings.anthropic_api_key is not None and "default_model" not in settings.model_fields_set:'),
    ('W17 ambient base URL honoured', MAIN, '            base_url=ANTHROPIC_BASE_URL,\n', ''),
    ('W18 close only on a clean exit', MAIN, '        try:\n            yield\n        finally:\n            if owned is not None:\n                await owned.close()\n', '        yield\n        if owned is not None:\n            await owned.close()\n'),
    ('S12 haiku added', STATE, '    Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10")),\n', '    Price("claude-sonnet-5-5", date(2026, 11, 3), Decimal("2"), Decimal("10")),\n    Price("claude-haiku-5-5", date(2026, 11, 3), Decimal("0.10"), Decimal("0.50")),\n'),
    ('S13 fake-echo dropped with Claude', STATE, '        state.prices.add(price)\n', '        state.prices.add(price)\n    state.catalog.pop("fake-echo", None)\n'),
    ('K05 blank key enables Claude', CFG, '        return key if key is not None and key.get_secret_value() else None\n', '        return key\n'),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", "-p", "no:cacheprovider"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8", newline="\n")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True, timeout=300).returncode
    finally:
        path.write_text(original, encoding="utf-8", newline="\n")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
