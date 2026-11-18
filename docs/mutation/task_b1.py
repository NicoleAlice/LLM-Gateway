"""Mutation run for plan 1b-B task B1 (AnthropicProvider and the 400 mapping in /v1/chat).

Apply, run pytest, restore. pytest runs with this interpreter (``sys.executable``), not
``uv run``, so an environment sync failure can't be mistaken for a killed mutant."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
S = ROOT / "src" / "llm_gateway"
PROV, BASE, CHAT = S / "providers" / "anthropic.py", S / "providers" / "base.py", S / "api" / "chat.py"

MUTANTS = [
    # Request shape
    ("P01 max_tokens not forwarded", PROV, "max_tokens=request.max_tokens,", "max_tokens=1024,"),
    ("P02 routed model not sent", PROV, "                model=model,\n", '                model="claude-opus-5-5",\n'),
    ("P03 system prompts joined without blank line", PROV, 'return "\\n\\n".join(leading), rest', 'return "\\n".join(leading), rest'),
    ("P04 only the first system prompt kept", PROV, 'return "\\n\\n".join(leading), rest', 'return leading[0] if leading else "", rest'),
    ("P05 empty system sent", PROV, "system=system or anthropic.omit,", "system=system,"),
    ("P06 every system message hoisted", PROV, 'if message.role == "system" and not rest:', 'if message.role == "system":'),
    ("P07 late system messages dropped", PROV, '            rest.append(SystemMessageParam(role="system", content=message.content))\n', "            pass\n"),
    ("P08 late system sent as user", PROV, 'SystemMessageParam(role="system", content=message.content)', 'UserMessageParam(role="user", content=message.content)'),
    ("P09 assistant sent as user", PROV, 'AssistantMessageParam(role="assistant", content=message.content)', 'UserMessageParam(role="user", content=message.content)'),
    ("P10 temperature sent", PROV, "                messages=messages,\n            )", "                messages=messages,\n                temperature=0.5,\n            )"),
    # Response
    ("R01 thinking leaks into content", PROV, 'content="".join(block.text for block in reply.content if block.type == "text"),', 'content="".join(getattr(b, "text", getattr(b, "thinking", "")) for b in reply.content),'),
    ("R02 only the first text block", PROV, 'content="".join(block.text for block in reply.content if block.type == "text"),', 'content=next((b.text for b in reply.content if b.type == "text"), ""),'),
    ("R03 routed model reported instead of served", PROV, "            model=reply.model,\n", "            model=model,\n"),
    ("R04 output tokens not billed", PROV, "output_tokens=usage.output_tokens)", "output_tokens=0)"),
    ("R05 input tokens not billed", PROV, "Usage(input_tokens=usage.input_tokens,", "Usage(input_tokens=0,"),
    ("R06 stop_reason dropped", PROV, 'stop_reason=reply.stop_reason or "end_turn",', 'stop_reason="end_turn",'),
    ("R07 missing stop_reason not defaulted", PROV, 'stop_reason=reply.stop_reason or "end_turn",', 'stop_reason=reply.stop_reason or "",'),
    ("R09 provider name wrong", PROV, '    name = "anthropic"\n', '    name = "claude"\n'),
    ("R11 any non-thinking block read as text", PROV, 'for block in reply.content if block.type == "text"),', 'for block in reply.content if block.type != "thinking"),'),
    ("R12 cached tokens not reported", PROV, "        if cached:\n", "        if False:\n"),
    ("R13 cache reads not counted", PROV, " + (usage.cache_read_input_tokens or 0)", ""),
    ("R14 cache writes not counted", PROV, "(usage.cache_creation_input_tokens or 0) + ", ""),
    ("R15 cache log on every answer", PROV, "        if cached:\n", "        if True:\n"),
    # Errors
    ("E01 422 not a rejection", PROV, "(anthropic.BadRequestError, anthropic.UnprocessableEntityError)", "(anthropic.BadRequestError,)"),
    ("E02 400 not a rejection", PROV, "(anthropic.BadRequestError, anthropic.UnprocessableEntityError)", "(anthropic.UnprocessableEntityError,)"),
    ("E03 every status a rejection", PROV, "(anthropic.BadRequestError, anthropic.UnprocessableEntityError)", "(anthropic.APIStatusError,)"),
    ("E04 upstream message in rejection", PROV, 'f"anthropic rejected the request ({type(exc).__name__})"', 'f"anthropic rejected the request ({exc})"'),
    ("E05 upstream message in failure", PROV, 'f"anthropic call failed ({type(exc).__name__})"', 'f"anthropic call failed ({exc})"'),
    ("E06 rejection raised with SDK context", PROV, '                f"anthropic rejected the request ({type(exc).__name__})"\n            )\n', '                f"anthropic rejected the request ({type(exc).__name__})"\n            )\n            raise failure\n'),
    ("E07 failure raised with SDK context", PROV, '            failure = ProviderError(f"anthropic call failed ({type(exc).__name__})")\n', '            failure = ProviderError(f"anthropic call failed ({type(exc).__name__})")\n            raise failure from exc\n'),
    ("E08 only SDK errors caught", PROV, "        except Exception as exc:\n", "        except anthropic.AnthropicError as exc:\n"),
    ("E09 rejection not a ProviderError", BASE, "class ProviderRejectedRequest(ProviderError):", "class ProviderRejectedRequest(Exception):"),
    # Route
    ("C01 rejection mapped to 502", CHAT, "    except ProviderRejectedRequest as exc:\n", "    except ZeroDivisionError as exc:\n"),
    ("C02 rejection status 422", CHAT, '400, "provider_rejected_request"', '422, "provider_rejected_request"'),
    ("C03 rejection code wrong", CHAT, '400, "provider_rejected_request"', '400, "bad_request"'),
    ("C04 stop_reason not exposed", CHAT, "        stop_reason=answer.stop_reason,\n", '        stop_reason="end_turn",\n'),
    ("C05 default stop_reason wrong", BASE, '    stop_reason: str = "end_turn"\n', '    stop_reason: str = "stop"\n'),
    ("C06 refusal not billed", CHAT, "    outcome.usage, outcome.cost_usd = answer.usage, amount\n", "    outcome.usage, outcome.cost_usd = answer.usage, (amount if answer.stop_reason != \"refusal\" else ZERO_COST)\n"),
    ("C07 rejection logged with its message", CHAT, "            model,\n            type(exc).__name__,\n        )", "            model,\n            exc,\n        )"),
    ("C08 rejection logged without provider/model", CHAT, '"provider %s rejected a request for model %s: %s",\n            provider_name,\n            model,', '"provider %s rejected a request for model %s: %s",\n            "?",\n            "?",'),
    ("C09 rejection raised with provider context", CHAT, '            400, "provider_rejected_request", "the model provider rejected the request"\n        )\n', '            400, "provider_rejected_request", "the model provider rejected the request"\n        )\n        raise failure\n'),
    ("C10 failure raised with provider context", CHAT, '        failure = ApiError(502, "provider_error", "the model provider failed to answer")\n', '        failure = ApiError(502, "provider_error", "the model provider failed to answer")\n        raise failure\n'),
    ("C11 failure not raised", CHAT, "    if failure is not None:\n", "    if False:\n"),
]

CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:warnings", "-p", "no:cacheprovider"]
for mid, path, old, new in MUTANTS:
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{mid}: pattern not found"
    assert old != new, f"{mid}: no-op"
    path.write_text(original.replace(old, new, 1), encoding="utf-8")
    try:
        rc = subprocess.run(CMD, cwd=ROOT, capture_output=True, timeout=300).returncode
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"{mid}\t{'killed' if rc else 'SURVIVED'}")
rc = subprocess.run(CMD, cwd=ROOT, capture_output=True).returncode
print("baseline after restore:", "green" if rc == 0 else "RED")
