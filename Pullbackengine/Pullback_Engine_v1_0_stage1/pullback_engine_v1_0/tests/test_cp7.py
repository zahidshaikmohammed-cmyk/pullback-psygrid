from __future__ import annotations

import asyncio
from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.cp5 import FunnelCounts, MonitoringState
from pullback_engine.cp6 import CP6OutputSystem
from pullback_engine.cp7 import CP7IntegrationEngine


class FakeEngine:
    def __init__(self, cycle):
        self.cycle = cycle
        self.running = True
        self.calls = 0
        self.stopped = False

    async def cycle_once(self, now=None):
        self.calls += 1
        return self.cycle

    def stop(self):
        self.stopped = True
        self.running = False

    def health(self):
        return {
            "engine": "RUNNING" if self.running else "STOPPED",
            "stocks_in_last_cycle": 1,
            "candidates": len(self.cycle.candidates),
            "new_signals": len(self.cycle.new_signals),
            "active_monitors": sum(s.active for s in self.cycle.monitoring.values()),
            "runtime_errors": 0,
        }


def make_cycle():
    ts = datetime(2026, 9, 12, 10, 30, tzinfo=IST)
    cp2 = type(
        "CP2",
        (),
        {
            "expected_stock_count": 450,
            "healthy_stock_count": 1,
            "stale_stock_count": 0,
            "nifty_error": None,
            "stocks": {"TEST": object()},
            "endpoints": {},
        },
    )()
    report = type(
        "Report",
        (),
        {
            "timestamp": ts,
            "universe": 450,
            "currently_calculable": 1,
            "temporarily_skipped": 449,
            "developing": 0,
            "qualified": 0,
            "armed": 0,
            "new_signals": 0,
            "hunter_status": "RUNNING",
            "funnel": FunnelCounts(450, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0),
        },
    )()
    monitor = MonitoringState(
        setup_id="TEST-1",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=100.0,
    )
    return type(
        "Cycle",
        (),
        {
            "timestamp": ts,
            "cp2_cycle": cp2,
            "candidates": [],
            "new_signals": [],
            "monitoring": {"TEST-1": monitor},
            "report": report,
        },
    )()


def test_cp7_single_cycle_composes_cp5_and_cp6():
    engine = FakeEngine(make_cycle())
    cp7 = CP7IntegrationEngine(engine=engine, output=CP6OutputSystem())
    result = asyncio.run(cp7.cycle_once(make_cycle().timestamp))
    assert engine.calls == 1
    assert result.timestamp == make_cycle().timestamp
    assert result.snapshot.panel_1
    assert result.snapshot.panel_2
    assert result.snapshot.panel_3
    assert result.snapshot.panel_4
    assert result.healthy


def test_cp7_does_not_mutate_cp5_cycle():
    cycle = make_cycle()
    engine = FakeEngine(cycle)
    cp7 = CP7IntegrationEngine(engine=engine)
    before = (cycle.timestamp, len(cycle.candidates), len(cycle.new_signals), len(cycle.monitoring))
    asyncio.run(cp7.cycle_once(cycle.timestamp))
    after = (cycle.timestamp, len(cycle.candidates), len(cycle.new_signals), len(cycle.monitoring))
    assert before == after


def test_cp7_detects_duplicate_signal_ids():
    cycle = make_cycle()
    signal = type("Signal", (), {"setup_id": "DUP", "symbol": "TEST", "direction": "LONG"})()
    cycle.new_signals = [signal, signal]
    errors = CP7IntegrationEngine._validate_cycle(cycle)
    assert "duplicate_signal_setup_id_in_cycle" in errors


def test_cp7_detects_missing_monitor_for_signal():
    cycle = make_cycle()
    signal = type("Signal", (), {"setup_id": "MISSING", "symbol": "TEST", "direction": "LONG"})()
    cycle.new_signals = [signal]
    cycle.monitoring = {}
    errors = CP7IntegrationEngine._validate_cycle(cycle)
    assert "signal_without_monitor:MISSING" in errors


def test_cp7_detects_monitor_mismatch():
    cycle = make_cycle()
    signal = type("Signal", (), {"setup_id": "TEST-1", "symbol": "TEST", "direction": "SHORT"})()
    cycle.new_signals = [signal]
    errors = CP7IntegrationEngine._validate_cycle(cycle)
    assert "monitor_mismatch:TEST-1" in errors


def test_cp7_detects_bad_report_funnel():
    cycle = make_cycle()
    cycle.report.funnel = FunnelCounts(449, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0)
    errors = CP7IntegrationEngine._validate_cycle(cycle)
    assert "report_funnel_universe_mismatch" in errors


def test_cp7_requires_timezone_aware_cycle():
    cycle = make_cycle()
    cycle.timestamp = datetime(2026, 9, 12, 10, 30)
    errors = CP7IntegrationEngine._validate_cycle(cycle)
    assert "cycle_timestamp_not_timezone_aware" in errors


def test_cp7_render_last_requires_cycle():
    cp7 = CP7IntegrationEngine(engine=FakeEngine(make_cycle()))
    try:
        cp7.render_last(clear=False)
    except RuntimeError as exc:
        assert "no CP7 cycle" in str(exc)
    else:
        raise AssertionError("render_last should require a completed cycle")


def test_cp7_health_after_cycle():
    cp7 = CP7IntegrationEngine(engine=FakeEngine(make_cycle()))
    asyncio.run(cp7.cycle_once(make_cycle().timestamp))
    health = cp7.health()
    assert health["last_cycle_healthy"] is True
    assert health["cp6_snapshot_available"] is True
    assert health["integration_errors"] == 0


def test_cp7_stop_stops_underlying_engine():
    engine = FakeEngine(make_cycle())
    cp7 = CP7IntegrationEngine(engine=engine)
    cp7.running = True
    cp7.stop()
    assert cp7.running is False
    assert engine.stopped is True


def test_cp7_output_is_cp6_snapshot_only():
    cp7 = CP7IntegrationEngine(engine=FakeEngine(make_cycle()))
    result = asyncio.run(cp7.cycle_once(make_cycle().timestamp))
    assert type(result.snapshot).__name__ == "CP6Snapshot"


def test_cp7_cycle_preserves_zero_signal_cycle():
    cycle = make_cycle()
    assert cycle.new_signals == []
    cp7 = CP7IntegrationEngine(engine=FakeEngine(cycle))
    result = asyncio.run(cp7.cycle_once(cycle.timestamp))
    assert result.cp5_cycle.new_signals == []
    assert result.healthy
