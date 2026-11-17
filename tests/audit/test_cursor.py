import base64
from datetime import UTC, datetime, timedelta, timezone

import pytest

from llm_gateway.audit.cursor import MAX_CURSOR_LENGTH, decode_cursor, encode_cursor
from llm_gateway.audit.records import AuditPosition

ID = "0123456789abcdef0123456789abcdef"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_cursor_roundtrip() -> None:
    pos = AuditPosition(datetime(2026, 11, 17, 12, 0, 0, 123456, tzinfo=UTC), ID)
    assert decode_cursor(encode_cursor(pos)) == pos


def test_a_row_stored_with_another_offset_still_gets_a_valid_cursor() -> None:
    # AuditRecord only requires an aware timestamp, so encode must normalize to UTC or
    # the canonical check would reject the server's own cursor.
    sao_paulo = timezone(timedelta(hours=-3))
    pos = AuditPosition(datetime(2026, 11, 17, 9, tzinfo=sao_paulo), ID)
    assert decode_cursor(encode_cursor(pos)) == pos


def test_cursor_is_url_safe_and_short() -> None:
    raw = encode_cursor(AuditPosition(datetime(2026, 11, 17, 12, tzinfo=UTC), ID))
    assert raw.replace("-", "").replace("_", "").isalnum() and len(raw) < MAX_CURSOR_LENGTH


def test_cursor_reveals_nothing_beyond_the_row() -> None:
    # Only the row's own timestamp and id: no global counter of other tenants' writes.
    raw = encode_cursor(AuditPosition(datetime(2026, 11, 17, 12, tzinfo=UTC), ID))
    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    assert decoded == '{"t":"2026-11-17T12:00:00+00:00","i":"' + ID + '"}'


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "!!!",
        "a",
        b64("not json"),
        b64("[1, 2]"),
        b64('{"i": "' + ID + '"}'),
        b64('{"t": "2026-11-17T12:00:00+00:00"}'),
        b64('{"t":"2026-11-17T12:00:00","i":"' + ID + '"}'),  # naive
        b64('{"t":"2026-11-17T12:00:00+00:00","i":"' + ID.upper() + '"}'),
        b64('{"t":"2026-11-17T12:00:00+00:00","i":"short"}'),
        b64('{"t":"2026-11-17T12:00:00+00:00","i":7}'),
        b64('{"t":12,"i":"' + ID + '"}'),
        b64('{"t":"yesterday","i":"' + ID + '"}'),
        b64('{"t":"2026-11-17T12:00:00+00:00","i":"' + ID + '","x":0}'),
        # edges where converting to UTC overflows: must be ValueError, not OverflowError
        b64('{"t":"0001-01-01T00:00:00+05:00","i":"' + ID + '"}'),
        b64('{"t":"9999-12-31T23:59:59-05:00","i":"' + ID + '"}'),
        # non-canonical encodings of a valid position
        encode_cursor(AuditPosition(datetime(2026, 11, 17, 12, tzinfo=UTC), ID)) + "==",
        b64('{"t":"2026-11-17T09:00:00-03:00","i":"' + ID + '"}'),
        b64('{"i":"' + ID + '","t":"2026-11-17T12:00:00+00:00"}'),
        b64('{"t":"2026-11-17T12:00:00+00:00", "i":"' + ID + '"}'),
        b64('{"t":"x","t":"2026-11-17T12:00:00+00:00","i":"' + ID + '"}'),
        "a" * (MAX_CURSOR_LENGTH + 1),
    ],
)
def test_bad_cursor_raises_value_error(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(raw)


def test_a_json_list_with_the_right_names_is_rejected_not_a_crash() -> None:
    # ["t", "i"] holds the key names as values: without the dict check, data["t"] is a
    # TypeError (a 500), not a ValueError.
    with pytest.raises(ValueError):
        decode_cursor(b64('["t","i"]'))


def test_oversize_cursor_is_refused_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    # No canonical cursor is anywhere near 256 chars, so the limit's only job is to avoid
    # decoding arbitrarily long attacker input: pin that it refuses without decoding.
    import llm_gateway.audit.cursor as cursor_module

    def fail(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("decoded an oversize cursor")

    monkeypatch.setattr(cursor_module.base64, "urlsafe_b64decode", fail)
    with pytest.raises(ValueError, match="length"):
        decode_cursor("A" * (MAX_CURSOR_LENGTH + 1))


def test_junk_inside_a_valid_cursor_is_rejected() -> None:
    good = encode_cursor(AuditPosition(datetime(2026, 11, 17, 12, tzinfo=UTC), ID))
    with pytest.raises(ValueError):
        decode_cursor(good[:5] + "!" + good[5:])
