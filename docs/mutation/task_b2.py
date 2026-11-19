"""Mutation run for plan 1b-B task B2 (provider timeout).

Apply, run pytest, restore. pytest runs with this interpreter (``sys.executable``), not
``uv run``, so an environment sync failure can't be mistaken for a killed mutant.
T01 removes the deadline, so its run waits for the 30 s fake hang: expect it to be slow."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
CHAT, CFG = S / "api" / "chat.py", S / "config.py"

MUTANTS = [
    ('T01 no deadline', CHAT, 'anyio.move_on_after(timeout_s)', 'anyio.move_on_after(None)'),
    ('T02 setting ignored', CHAT, '    timeout_s = state.settings.provider_timeout_s\n', '    timeout_s = 0.2\n'),
    ('T03 timeout is a 502', CHAT, 'raise ApiError(504, "provider_timeout", "the model provider did not answer in time")', 'raise ApiError(502, "provider_timeout", "the model provider did not answer in time")'),
    ('T04 timeout code wrong', CHAT, 'raise ApiError(504, "provider_timeout", "the model provider did not answer in time")', 'raise ApiError(504, "provider_error", "the model provider did not answer in time")'),
    ('T05 only a clean cancellation counts', CHAT, '    if not answered and deadline.cancel_called:\n', '    if not answered and deadline.cancelled_caught:\n'),
    ('T06 late answer thrown away', CHAT, '    if not answered and deadline.cancel_called:\n', '    if deadline.cancel_called:\n'),
    ('T07 answer never marked', CHAT, '            answered = True\n', '            pass\n'),
    ('T08 provider TimeoutError counted as ours', CHAT, '    if not answered and deadline.cancel_called:\n', '    if not answered and (deadline.cancel_called or error is TimeoutError):\n'),
    ('T09 timeout not logged', CHAT, '        logger.warning(\n            "provider %s timed out', '        (lambda *a: None)(\n            "provider %s timed out'),
    ('T10 timeout check skipped', CHAT, '    if not answered and deadline.cancel_called:\n', '    if False:\n'),
    ('K01 floor below 1 s', CFG, 'provider_timeout_s: float = Field(120, ge=1, le=600)', 'provider_timeout_s: float = Field(120, ge=0, le=600)'),
    ('K02 ceiling above 600 s', CFG, 'provider_timeout_s: float = Field(120, ge=1, le=600)', 'provider_timeout_s: float = Field(120, ge=1, le=6000)'),
    ('K03 default not 120 s', CFG, 'provider_timeout_s: float = Field(120, ge=1, le=600)', 'provider_timeout_s: float = Field(60, ge=1, le=600)'),
    ('K04 no ceiling', CFG, 'provider_timeout_s: float = Field(120, ge=1, le=600)', 'provider_timeout_s: float = Field(120, ge=1)'),
    ('T11 hidden outer cancel lost', CHAT, '    await anyio.lowlevel.checkpoint_if_cancelled()\n', '\n'),
    ('T12 deadline 4 s longer', CHAT, 'anyio.move_on_after(timeout_s)', 'anyio.move_on_after(timeout_s + 4)'),
    ('T13 deadline doubled', CHAT, 'anyio.move_on_after(timeout_s)', 'anyio.move_on_after(timeout_s * 2)'),
    ('T14 deadline floored at 1 s', CHAT, 'anyio.move_on_after(timeout_s)', 'anyio.move_on_after(max(timeout_s, 1))'),
    ('T15 504 raised with provider context', CHAT, '    except Exception as exc:\n        error = type(exc)\n', '    except Exception as exc:\n        error = type(exc)\n        if deadline.cancel_called:\n            raise ApiError(504, "provider_timeout", "the model provider did not answer in time")\n'),
    ('T16 rejection checked before the deadline', CHAT, '    if not answered and deadline.cancel_called:\n', '    if not answered and deadline.cancel_called and not (error is not None and issubclass(error, ProviderRejectedRequest)):\n'),
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
