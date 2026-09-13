from __future__ import annotations

from datetime import datetime

from pullback_engine.core import IST
from run_service import _in_market_session, _next_market_start


def test_service_supervisor_is_market_session_aware():
    assert _in_market_session(datetime(2026, 9, 14, 10, 0, tzinfo=IST)) is True
    assert _in_market_session(datetime(2026, 9, 14, 16, 0, tzinfo=IST)) is False
    assert _in_market_session(datetime(2026, 9, 13, 10, 0, tzinfo=IST)) is False


def test_service_supervisor_skips_weekend_to_monday():
    sunday = datetime(2026, 9, 13, 18, 0, tzinfo=IST)
    assert _next_market_start(sunday) == datetime(2026, 9, 14, 9, 15, tzinfo=IST)


def test_service_supervisor_next_session_after_market_close():
    monday = datetime(2026, 9, 14, 16, 0, tzinfo=IST)
    assert _next_market_start(monday) == datetime(2026, 9, 15, 9, 15, tzinfo=IST)
