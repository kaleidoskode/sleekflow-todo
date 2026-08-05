from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.pagination import SortField, SortSpec, decode_cursor, encode_cursor

TODO_ID = UUID("018f3b2c-0000-7000-8000-000000000001")


def test_parse_ascending_sort():
    spec = SortSpec.parse("due_date")
    assert spec.field is SortField.DUE_DATE
    assert spec.descending is False


def test_parse_descending_sort():
    spec = SortSpec.parse("-priority")
    assert spec.field is SortField.PRIORITY
    assert spec.descending is True


def test_parse_rejects_unknown_field():
    with pytest.raises(ValueError):
        SortSpec.parse("created_at")


def test_cursor_round_trips_a_datetime():
    value = datetime(2026, 6, 1, 9, tzinfo=UTC)
    decoded_value, decoded_id = decode_cursor(encode_cursor(value, TODO_ID))
    assert decoded_value == value
    assert decoded_id == TODO_ID


def test_cursor_round_trips_an_integer():
    decoded_value, decoded_id = decode_cursor(encode_cursor(20, TODO_ID))
    assert decoded_value == 20
    assert decoded_id == TODO_ID


def test_cursor_round_trips_a_string():
    decoded_value, decoded_id = decode_cursor(encode_cursor("write the plan", TODO_ID))
    assert decoded_value == "write the plan"
    assert decoded_id == TODO_ID


def test_cursor_is_url_safe():
    cursor = encode_cursor(datetime(2026, 6, 1, tzinfo=UTC), TODO_ID)
    assert "=" not in cursor
    assert "/" not in cursor
    assert "+" not in cursor


def test_decode_rejects_malformed_cursor():
    with pytest.raises(ValueError):
        decode_cursor("not-a-real-cursor")
