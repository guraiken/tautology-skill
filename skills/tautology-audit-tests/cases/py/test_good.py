import pytest


@pytest.mark.parametrize("raw,expected", [("10", 10), ("20", 20)])
def test_parse_values(raw, expected):
    assert parse_int(raw) == expected


def test_parse_invalid_raises():
    with pytest.raises(ValueError, match="bad"):
        parse_bad("abc")


def test_rows_checked_before_loop():
    rows = fetch_local_rows()
    assert len(rows) > 0
    for row in rows:
        assert row["ok"]
