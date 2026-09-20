from datetime import datetime
from types import SimpleNamespace

from pullback_engine.core import IST
from pullback_engine.dashboard import DASHBOARD_PORT, DashboardState


def test_dashboard_contract_is_read_only_state_surface():
    state = DashboardState()
    snapshot = state.snapshot()
    assert snapshot["service"] == "PULLBACK_ENGINE_V1_0"
    assert snapshot["expected_stocks"] == 990
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


def test_dashboard_reports_cp5_cycle_errors():
    state = DashboardState()
    cp2 = SimpleNamespace(healthy_stock_count=450, expected_stock_count=450, stale_stock_count=0)
    cp5 = SimpleNamespace(
        cp2_cycle=cp2,
        candidates=[],
        monitoring={},
        new_signals=[],
        errors=["worker:RuntimeError:boom"],
        report=None,
    )
    cycle = SimpleNamespace(
        cp5_cycle=cp5,
        timestamp=datetime(2026, 9, 15, 10, 0, tzinfo=IST),
        integration_errors=(),
        healthy=False,
    )
    state.update(cycle)
    assert state.snapshot()["runtime_errors"] == 1
