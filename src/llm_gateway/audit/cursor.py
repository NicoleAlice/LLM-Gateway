"""Opaque pagination cursors for ``GET /v1/audit``.

A cursor is URL-safe base64 (no padding) of ``{"t": <ISO UTC timestamp>, "i": <row id>}``.
Only the exact encoding the server produces is accepted, so each position has one
cursor. It isn't signed: it holds nothing the caller can't already see (the row's
timestamp and id are in the page it came from), and editing one only moves the position
inside the caller's own tenant and window, which the repository always enforces.
Anything else raises ``ValueError`` (the route answers 422 ``invalid_cursor``).
"""

import base64
import binascii
import json
import re
from datetime import UTC, datetime

from llm_gateway.audit.records import AuditPosition

MAX_CURSOR_LENGTH = 256
_ID = re.compile(r"[0-9a-f]{32}")  # uuid4().hex, as written by the chat route


def encode_cursor(position: AuditPosition) -> str:
    payload = {"t": position.created_at.astimezone(UTC).isoformat(), "i": position.record_id}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(raw: str) -> AuditPosition:
    if not raw or len(raw) > MAX_CURSOR_LENGTH:
        raise ValueError("bad cursor length")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("bad cursor encoding") from exc
    # Just enough checking to build a position without crashing; the canonical check
    # below rejects everything else (extra keys, naive times, junk characters, padding).
    if not isinstance(data, dict) or not {"t", "i"} <= set(data):
        raise ValueError("bad cursor shape")
    timestamp, record_id = data["t"], data["i"]
    if not isinstance(record_id, str) or not _ID.fullmatch(record_id):
        raise ValueError("bad cursor id")
    if not isinstance(timestamp, str):
        raise ValueError("bad cursor time")
    try:
        # OverflowError: converting to UTC at the edges of year 1 / 9999.
        created_at = datetime.fromisoformat(timestamp).astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("bad cursor time") from exc
    position = AuditPosition(created_at, record_id)
    # Canonical form only: one cursor per position.
    if encode_cursor(position) != raw:
        raise ValueError("cursor is not in canonical form")
    return position
