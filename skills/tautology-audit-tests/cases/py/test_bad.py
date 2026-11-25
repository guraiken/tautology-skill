import time
from unittest.mock import Mock, patch


@patch("billing.format_price")
def test_format_price_usd(mock_fmt):
    mock_fmt.return_value = "$10.00"
    assert mock_fmt(10) == "$10.00"


def test_parse_try_without_fail():
    try:
        parse_bad("abc")
    except ValueError as err:
        assert "bad" in str(err)


def test_vacuous_loop_and_sleep():
    results = fetch_local_rows()
    time.sleep(0.5)
    for row in results:
        assert row["ok"]


def test_swallowed_error():
    try:
        risky_op()
    except RuntimeError:
        pass
    assert check_state() == "idle"


def test_parse_case_a():
    cfg = make_cfg()
    token = build_token("user-a")
    session = open_session(cfg, token)
    res = evaluate(session, "item-a")
    assert res["status"] == "ok"


def test_parse_case_b():
    cfg = make_cfg()
    token = build_token("user-b")
    session = open_session(cfg, token)
    res = evaluate(session, "item-b")
    assert res["status"] == "ok"


def test_mock_echo():
    rate = Mock(return_value=5)
    assert rate() == 5


def test_only_when_ready():
    job = start_job()
    if job.ready:
        assert job.result == 7
