from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.dashboard import DASHBOARD_PORT, DashboardState


def test_dashboard_contract_is_read_only_state_surface():
    state = DashboardState()
    snapshot = state.snapshot()
    assert snapshot["service"] == "PULLBACK_ENGINE_V1_0"
    assert snapshot["expected_stocks"] == 450
    assert snapshot["signals"] == []
    assert snapshot["events"] == []


def test_dashboard_state_waiting_is_market_aware():
    state = DashboardState()
    target = datetime(2026, 9, 14, 9, 15, tzinfo=IST)
    state.set_waiting(target)
    snapshot = state.snapshot()
    assert snapshot["engine"] == "WAITING"
    assert snapshot["session"] == "CLOSED"
    assert "09:15:00" in snapshot["events"][0]["message"]


def test_dashboard_port_is_separate_from_psygrid():
    assert DASHBOARD_PORT == 10001
