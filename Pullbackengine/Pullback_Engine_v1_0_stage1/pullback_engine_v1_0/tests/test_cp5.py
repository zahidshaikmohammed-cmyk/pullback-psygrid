from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from pullback_engine.core import Candle, IST
from pullback_engine.cp2 import CP2Cycle, StockData
from pullback_engine.cp4 import Signal
from pullback_engine.cp5 import (
    CP5ContinuousEngine,
    FunnelCounts,
    MonitoringState,
)


def candle(
    ts: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = 1000.0,
) -> Candle:
    return Candle(
        timestamp=ts,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
    )


def make_stock(
    symbol: str = "TEST",
    start: datetime | None = None,
    count: int = 5,
    price: float = 100.0,
    healthy: bool = True,
) -> StockData:
    start = start or datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    candles = [
        candle(
            start + timedelta(minutes=i),
            price,
            price + 1,
            price - 1,
            price,
            1000,
        )
        for i in range(count)
    ]

    return StockData(
        symbol=symbol,
        endpoint="A",
        candles_1m=candles,
        errors=[] if healthy else ["test_failure"],
        stale=not healthy,
        healthy=healthy,
        last_timestamp=candles[-1].timestamp,
    )


def make_cycle(
    stocks: dict[str, StockData] | None = None,
    nifty_payload: dict | None = None,
    nifty_error: str | None = None,
    expected: int | None = None,
) -> CP2Cycle:
    stocks = stocks or {}

    return CP2Cycle(
        timestamp=datetime(
            2026,
            9,
            12,
            10,
            0,
            tzinfo=IST,
        ),
        endpoints={},
        stocks=stocks,
        nifty_payload=nifty_payload,
        nifty_error=nifty_error,
        expected_stock_count=(
            expected
            if expected is not None
            else len(stocks)
        ),
    )


class FakeDataEngine:
    def __init__(self, cycle: CP2Cycle):
        self._cycle = cycle

    async def cycle(self, now=None):
        return self._cycle


def test_monitoring_state_defaults():
    ts = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    state = MonitoringState(
        setup_id="TEST-PB-001",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=100.0,
    )

    assert state.status == "TREND_VALID_AT_TRIGGER"
    assert state.invalidation_consecutive == 0
    assert state.last_evaluated_5m is None
    assert state.active is True


def test_engine_initializes_running(tmp_path: Path):
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "state.json",
    )

    assert engine.running is True
    assert engine.monitoring == {}
    assert engine.alerted_setup_ids == set()
    assert engine.last_cycle is None


def test_state_file_not_required_on_first_start(tmp_path: Path):
    path = tmp_path / "missing.json"

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    assert not path.exists()
    assert engine.monitoring == {}


def test_state_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"

    ts = datetime(
        2026,
        9,
        12,
        10,
        5,
        tzinfo=IST,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    engine.alerted_setup_ids.add("SETUP-001")

    engine.monitoring["SETUP-001"] = MonitoringState(
        setup_id="SETUP-001",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=105.5,
        status="TREND_VALID",
        invalidation_consecutive=1,
        last_evaluated_5m=ts,
        active=True,
    )

    engine._save_state()

    assert path.exists()

    restored = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    assert "SETUP-001" in restored.alerted_setup_ids
    assert "SETUP-001" in restored.monitoring

    state = restored.monitoring["SETUP-001"]

    assert state.symbol == "TEST"
    assert state.direction == "LONG"
    assert state.entry_price == 105.5
    assert state.status == "TREND_VALID"
    assert state.invalidation_consecutive == 1
    assert state.active is True


def test_corrupt_state_does_not_crash_engine(tmp_path: Path):
    path = tmp_path / "state.json"

    path.write_text(
        "{not valid json",
        encoding="utf-8",
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    assert engine.running is True
    assert engine.monitoring == {}
    assert len(engine.runtime_errors) == 1


def test_invalid_monitoring_row_isolated(tmp_path: Path):
    path = tmp_path / "state.json"

    payload = {
        "version": 1,
        "alerted_setup_ids": [],
        "monitoring": {
            "BAD": {
                "setup_id": "BAD",
                "symbol": "TEST",
                "direction": "LONG",
                "signal_timestamp": "not-a-date",
                "entry_price": 100,
            }
        },
    }

    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    assert engine.monitoring == {}
    assert engine.running is True


def test_save_state_uses_atomic_replace(tmp_path: Path):
    path = tmp_path / "state.json"

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=path,
    )

    engine._save_state()

    assert path.exists()
    assert not Path(
        str(path) + ".tmp"
    ).exists()


def test_stop_sets_running_false(tmp_path: Path):
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "state.json",
    )

    engine.stop()

    assert engine.running is False


def test_worker_limit_is_clamped(tmp_path: Path):
    engine_low = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "a.json",
        worker_limit=0,
    )

    engine_high = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "b.json",
        worker_limit=9999,
    )

    assert engine_low.worker_limit == 1
    assert engine_high.worker_limit == 990


def test_single_stock_cycle_preserves_nifty():
    stock = make_stock()

    original = make_cycle(
        stocks={"TEST": stock},
        nifty_payload={"5m": []},
        nifty_error=None,
        expected=450,
    )

    result = CP5ContinuousEngine._single_stock_cycle(
        original,
        stock,
    )

    assert result.stocks == {"TEST": stock}
    assert result.nifty_payload == {"5m": []}
    assert result.nifty_error is None
    assert result.expected_stock_count == 450


def test_engine_panel_contains_market_health():
    stock = make_stock()

    cycle = type(
        "Cycle",
        (),
        {
            "timestamp": datetime(
                2026,
                9,
                12,
                10,
                0,
                tzinfo=IST,
            ),
            "cp2_cycle": make_cycle(
                stocks={"TEST": stock},
                expected=1,
            ),
        },
    )()

    text = CP5ContinuousEngine.format_engine_panel(
        cycle
    )

    assert "PANEL 1 — ENGINE / MARKET" in text
    assert "Engine: RUNNING" in text
    assert "NIFTY regime data: AVAILABLE" in text
    assert "1/1 healthy" in text


def test_signal_panel_empty():
    ts = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    cycle = type(
        "Cycle",
        (),
        {
            "timestamp": ts,
            "new_signals": [],
            "monitoring": {},
        },
    )()

    text = CP5ContinuousEngine.format_signal_panel(
        cycle
    )

    assert "PANEL 3 — SIGNAL / MONITOR" in text
    assert "New signals this cycle: 0" in text
    assert "Active triggered setups: 0" in text


def test_cycle_panel_none():
    text = CP5ContinuousEngine.format_cycle_panel(
        None
    )

    assert "PANEL 4 — 15-MINUTE CYCLE" in text
    assert "No report due this minute." in text


def test_report_due_only_on_quarter_hour():
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        )
    )

    at_1000 = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    at_1001 = datetime(
        2026,
        9,
        12,
        10,
        1,
        tzinfo=IST,
    )

    assert engine._report_due(
        at_1000,
        None,
    ) == at_1000

    assert engine._report_due(
        at_1001,
        None,
    ) is None


def test_report_due_deduplicates_same_slot():
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        )
    )

    ts = datetime(
        2026,
        9,
        12,
        10,
        15,
        tzinfo=IST,
    )

    assert engine._report_due(
        ts,
        None,
    ) == ts

    assert engine._report_due(
        ts,
        ts,
    ) is None


def test_report_not_due_before_hunt_start():
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        )
    )

    ts = datetime(
        2026,
        9,
        12,
        9,
        45,
        tzinfo=IST,
    )

    assert engine._report_due(
        ts,
        None,
    ) is None


def test_report_due_at_market_end():
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        )
    )

    ts = datetime(
        2026,
        9,
        12,
        15,
        15,
        tzinfo=IST,
    )

    assert engine._report_due(
        ts,
        None,
    ) == ts


def test_candidate_funnel_empty_universe(tmp_path: Path):
    cycle = make_cycle(
        stocks={},
        expected=450,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    counts = engine._candidate_funnel(
        cycle,
        {},
        [],
        [],
        datetime(
            2026,
            9,
            12,
            10,
            0,
            tzinfo=IST,
        ),
    )

    assert isinstance(
        counts,
        FunnelCounts,
    )
    assert counts.universe == 450
    assert counts.valid_data == 0
    assert counts.signals == 0


def test_candidate_funnel_counts_healthy_stock(
    tmp_path: Path,
):
    stock = make_stock(
        count=6,
        price=100,
    )

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    counts = engine._candidate_funnel(
        cycle,
        {},
        [],
        [],
        datetime(
            2026,
            9,
            12,
            10,
            6,
            tzinfo=IST,
        ),
    )

    assert counts.universe == 1
    assert counts.valid_data == 1
    assert counts.price_eligible == 1


def test_candidate_funnel_excludes_unhealthy_stock(
    tmp_path: Path,
):
    stock = make_stock(
        healthy=False
    )

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    counts = engine._candidate_funnel(
        cycle,
        {},
        [],
        [],
        datetime(
            2026,
            9,
            12,
            10,
            6,
            tzinfo=IST,
        ),
    )

    assert counts.valid_data == 0
    assert counts.price_eligible == 0


def test_run_one_stock_fault_isolated(tmp_path: Path):
    stock = make_stock()

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    class BrokenWorker:
        def cycle(self, *args, **kwargs):
            raise RuntimeError("boom")

    engine.worker_engines["TEST"] = BrokenWorker()

    (
        symbol,
        result,
        candidates,
        errors,
    ) = engine._run_one_stock(
        "TEST",
        stock,
        cycle,
        datetime(
            2026,
            9,
            12,
            10,
            5,
            tzinfo=IST,
        ),
    )

    assert symbol == "TEST"
    assert result is None
    assert candidates == []
    assert any(
        "RuntimeError" in error
        for error in errors
    )


def test_process_universe_isolates_broken_stock(
    tmp_path: Path,
):
    good = make_stock(
        symbol="GOOD",
        count=6,
    )

    bad = make_stock(
        symbol="BAD",
        count=6,
    )

    cycle = make_cycle(
        stocks={
            "GOOD": good,
            "BAD": bad,
        },
        expected=2,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    original = engine._run_one_stock

    def fake_run(
        symbol,
        stock,
        cp2_cycle,
        now,
    ):
        if symbol == "BAD":
            raise RuntimeError("bad stock")

        return original(
            symbol,
            stock,
            cp2_cycle,
            now,
        )

    engine._run_one_stock = fake_run

    (
        stock_results,
        candidates,
        signals,
        errors,
    ) = asyncio.run(
        engine._process_universe(
            cycle,
            datetime(
                2026,
                9,
                12,
                10,
                5,
                tzinfo=IST,
            ),
        )
    )

    assert "GOOD" in stock_results
    assert "BAD" not in stock_results
    assert signals == []

    assert any(
        "universe_worker" in error
        for error in errors
    )


def test_update_monitoring_adds_new_signal(
    tmp_path: Path,
):
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "state.json",
    )

    ts = datetime(
        2026,
        9,
        12,
        10,
        5,
        tzinfo=IST,
    )

    signal = Signal(
        setup_id="TEST-PB-003",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=101.0,
        trend_invalidation_status=(
            "TREND_VALID_AT_TRIGGER"
        ),
    )

    engine._update_monitoring(
        make_cycle(),
        [signal],
    )

    assert "TEST-PB-003" in engine.monitoring

    assert (
        engine.monitoring[
            "TEST-PB-003"
        ].entry_price
        == 101.0
    )


def test_update_monitoring_does_not_duplicate_setup(
    tmp_path: Path,
):
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "state.json",
    )

    ts = datetime(
        2026,
        9,
        12,
        10,
        5,
        tzinfo=IST,
    )

    state = MonitoringState(
        setup_id="TEST-PB-004",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=100.0,
    )

    engine.monitoring[
        "TEST-PB-004"
    ] = state

    signal = Signal(
        setup_id="TEST-PB-004",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=110.0,
        trend_invalidation_status=(
            "TREND_VALID_AT_TRIGGER"
        ),
    )

    engine._update_monitoring(
        make_cycle(),
        [signal],
    )

    assert (
        engine.monitoring[
            "TEST-PB-004"
        ].entry_price
        == 100.0
    )


def test_health_initial_state(tmp_path: Path):
    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(
            make_cycle()
        ),
        state_path=tmp_path / "state.json",
    )

    health = engine.health()

    assert health["engine"] == "RUNNING"
    assert health["stocks_in_last_cycle"] == 0
    assert health["candidates"] == 0
    assert health["new_signals"] == 0
    assert health["active_monitors"] == 0
    assert health["invalidated_monitors"] == 0
    assert health["alerted_setup_ids"] == 0
    assert health["runtime_errors"] == 0


def test_cycle_once_completes_without_crashing(
    tmp_path: Path,
):
    stock = make_stock(
        symbol="TEST",
        count=6,
        price=100,
    )

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    result = asyncio.run(
        engine.cycle_once(
            now=datetime(
                2026,
                9,
                12,
                10,
                6,
                tzinfo=IST,
            )
        )
    )

    assert result.timestamp.hour == 10
    assert result.cp2_cycle is cycle
    assert result.stock_results is not None
    assert result.monitoring is not None
    assert engine.last_cycle is result


def test_cycle_once_persists_state(
    tmp_path: Path,
):
    path = tmp_path / "state.json"

    cycle = make_cycle(
        stocks={
            "TEST": make_stock(
                symbol="TEST",
                count=6,
            )
        },
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=path,
    )

    asyncio.run(
        engine.cycle_once(
            now=datetime(
                2026,
                9,
                12,
                10,
                6,
                tzinfo=IST,
            )
        )
    )

    assert path.exists()

    payload = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    assert payload["version"] == 1
    assert "alerted_setup_ids" in payload
    assert "monitoring" in payload


def test_engine_panel_reports_endpoint_failures():
    stock = make_stock()

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    cycle.endpoints["A"] = type(
        "Endpoint",
        (),
        {"error": None},
    )()

    cycle.endpoints["B"] = type(
        "Endpoint",
        (),
        {"error": "connection_failed"},
    )()

    wrapper = type(
        "Cycle",
        (),
        {
            "timestamp": cycle.timestamp,
            "cp2_cycle": cycle,
        },
    )()

    text = CP5ContinuousEngine.format_engine_panel(
        wrapper
    )

    assert "A=OK" in text
    assert "B=FAIL" in text


def test_signal_panel_shows_active_monitor():
    ts = datetime(
        2026,
        9,
        12,
        10,
        5,
        tzinfo=IST,
    )

    state = MonitoringState(
        setup_id="TEST-PB-005",
        symbol="TEST",
        direction="SHORT",
        signal_timestamp=ts,
        entry_price=99.0,
        status="TREND_VALID",
        active=True,
    )

    cycle = type(
        "Cycle",
        (),
        {
            "new_signals": [],
            "monitoring": {
                "TEST-PB-005": state
            },
        },
    )()

    text = CP5ContinuousEngine.format_signal_panel(
        cycle
    )

    assert "Active triggered setups: 1" in text
    assert "TEST | SHORT" in text
    assert "TEST-PB-005" in text


def test_signal_panel_excludes_inactive_monitor():
    ts = datetime(
        2026,
        9,
        12,
        10,
        5,
        tzinfo=IST,
    )

    state = MonitoringState(
        setup_id="TEST-PB-006",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=ts,
        entry_price=101.0,
        status="INVALIDATED",
        active=False,
    )

    cycle = type(
        "Cycle",
        (),
        {
            "new_signals": [],
            "monitoring": {
                "TEST-PB-006": state
            },
        },
    )()

    text = CP5ContinuousEngine.format_signal_panel(
        cycle
    )

    assert "Active triggered setups: 0" in text


def test_hunter_panel_empty():
    ts = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    cycle = type(
        "Cycle",
        (),
        {
            "timestamp": ts,
            "candidates": [],
        },
    )()

    text = CP5ContinuousEngine.format_hunter_panel(
        cycle
    )

    assert "PANEL 2 — PULLBACK HUNTER" in text
    assert "Developing: 0" in text
    assert "Qualified: 0" in text
    assert "Trigger-armed: 0" in text


def test_cycle_report_format_contains_funnel():
    report = type(
        "Report",
        (),
        {
            "timestamp": datetime(
                2026,
                9,
                12,
                10,
                15,
                tzinfo=IST,
            ),
            "universe": 450,
            "currently_calculable": 440,
            "temporarily_skipped": 10,
            "developing": 5,
            "qualified": 3,
            "armed": 2,
            "new_signals": 1,
            "hunter_status": "RUNNING",
            "funnel": FunnelCounts(
                universe=450,
                price_eligible=300,
                valid_data=440,
                regime=20,
                impulse=15,
                pullback=10,
                structure=8,
                trend=7,
                early_entry=5,
                reacceleration=2,
                signals=1,
            ),
        },
    )()

    text = CP5ContinuousEngine.format_cycle_panel(
        report
    )

    assert "450" in text
    assert "440" in text
    assert "10" in text
    assert "300 price" in text
    assert "1 SIGNALS" in text


def test_report_build_uses_expected_universe(
    tmp_path: Path,
):
    cycle = make_cycle(
        stocks={},
        expected=450,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    report = engine._build_report(
        datetime(
            2026,
            9,
            12,
            10,
            15,
            tzinfo=IST,
        ),
        cycle,
        [],
        [],
        {},
    )

    assert report.universe == 450
    assert report.currently_calculable == 0
    assert report.temporarily_skipped == 450
    assert report.new_signals == 0
    assert report.hunter_status == "RUNNING"


def test_report_build_counts_candidate_states(
    tmp_path: Path,
):
    cycle = make_cycle(
        stocks={},
        expected=0,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    candidates = [
        type(
            "Candidate",
            (),
            {
                "state": "IMPULSE_DETECTED",
                "impulse": object(),
                "pullback": None,
                "stock_trend": None,
                "nifty_regime": None,
                "symbol": "TEST",
                "direction": "LONG",
                "setup_id": "TEST-1",
            },
        )(),
        type(
            "Candidate",
            (),
            {
                "state": "QUALIFIED_PULLBACK",
                "impulse": object(),
                "pullback": type(
                    "PB",
                    (),
                    {"structure": object()},
                )(),
                "stock_trend": True,
                "nifty_regime": True,
                "symbol": "TEST",
                "direction": "LONG",
                "setup_id": "TEST-2",
            },
        )(),
        type(
            "Candidate",
            (),
            {
                "state": "TRIGGER_ARMED",
                "impulse": object(),
                "pullback": type(
                    "PB",
                    (),
                    {"structure": object()},
                )(),
                "stock_trend": True,
                "nifty_regime": True,
                "symbol": "TEST",
                "direction": "LONG",
                "setup_id": "TEST-3",
            },
        )(),
    ]

    report = engine._build_report(
        datetime(
            2026,
            9,
            12,
            10,
            15,
            tzinfo=IST,
        ),
        cycle,
        candidates,
        [],
        {},
    )

    assert report.developing == 1
    assert report.qualified == 1
    assert report.armed == 1


def test_health_after_cycle(tmp_path: Path):
    stock = make_stock(
        symbol="TEST",
        count=6,
    )

    cycle = make_cycle(
        stocks={"TEST": stock},
        expected=1,
    )

    engine = CP5ContinuousEngine(
        data_engine=FakeDataEngine(cycle),
        state_path=tmp_path / "state.json",
    )

    asyncio.run(
        engine.cycle_once(
            now=datetime(
                2026,
                9,
                12,
                10,
                6,
                tzinfo=IST,
            )
        )
    )

    health = engine.health()

    assert health["engine"] == "RUNNING"
    assert health["stocks_in_last_cycle"] >= 0
    assert health["runtime_errors"] >= 0