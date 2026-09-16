from __future__ import annotations

import asyncio
from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.cp2 import CP2Cycle, StockData
from pullback_engine.cp5 import CP5ContinuousEngine


def test_cp5_schedules_all_450_stocks_in_a_cycle(monkeypatch, tmp_path):
    engine = CP5ContinuousEngine(state_path=tmp_path / "state.json", worker_limit=450)
    now = datetime(2026, 9, 16, 10, 0, tzinfo=IST)
    stocks = {
        f"STK{i:03d}": StockData(
            symbol=f"STK{i:03d}",
            endpoint="test",
            healthy=False,
        )
        for i in range(450)
    }
    cp2 = CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks=stocks,
        nifty_payload=None,
        nifty_error="test",
        expected_stock_count=450,
    )

    scheduled: list[str] = []

    def fake_run(symbol, stock, cp2_cycle, cycle_now):
        scheduled.append(symbol)
        return symbol, None, [], ["__SIGNALS__:0"]

    monkeypatch.setattr(engine, "_run_one_stock", fake_run)

    asyncio.run(engine._process_universe(cp2, now))

    assert len(scheduled) == 450
    assert set(scheduled) == set(stocks)
    assert engine.worker_limit == 450


def test_cp5_never_allows_worker_limit_above_450(tmp_path):
    engine = CP5ContinuousEngine(state_path=tmp_path / "state.json", worker_limit=9999)
    assert engine.worker_limit == 450
