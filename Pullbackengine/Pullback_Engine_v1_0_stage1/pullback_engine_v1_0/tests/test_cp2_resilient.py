import asyncio
from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.cp2 import CP2Cycle, EndpointData, EXPECTED_STOCKS, LIVE_NAME, StockData
from pullback_engine.cp2_resilient import ResilientCP2DataEngine

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=IST)


def cycle_with_count(count: int) -> CP2Cycle:
    endpoint = EndpointData(
        name=LIVE_NAME,
        url=LIVE_NAME,
        stock_count=count,
        symbols=[],
        stocks={},
        error=None if count == EXPECTED_STOCKS else "partial",
        healthy=count == EXPECTED_STOCKS,
    )
    stocks = {
        f"S{i:03d}": StockData(
            symbol=f"S{i:03d}",
            endpoint=LIVE_NAME,
            healthy=True,
        )
        for i in range(count)
    }
    return CP2Cycle(
        timestamp=NOW,
        endpoints={LIVE_NAME: endpoint},
        stocks=stocks,
        nifty_payload={"symbol": "NIFTY"},
        nifty_error=None,
        expected_stock_count=EXPECTED_STOCKS,
    )


def test_resilient_cp2_retries_incomplete_universe(monkeypatch):
    calls = []
    cycles = [cycle_with_count(728), cycle_with_count(728), cycle_with_count(EXPECTED_STOCKS)]

    async def fake_cycle(self, now=None):
        calls.append(1)
        return cycles.pop(0)

    monkeypatch.setattr("pullback_engine.cp2.CP2DataEngine.cycle", fake_cycle)

    engine = ResilientCP2DataEngine()
    result = asyncio.run(engine.cycle(NOW))

    assert len(calls) == 3
    assert result.unique_stock_count == EXPECTED_STOCKS
    assert ResilientCP2DataEngine._coverage_complete(result)


def test_resilient_cp2_returns_last_snapshot_after_retry_budget(monkeypatch):
    calls = []

    async def fake_cycle(self, now=None):
        calls.append(1)
        return cycle_with_count(728)

    monkeypatch.setattr("pullback_engine.cp2.CP2DataEngine.cycle", fake_cycle)

    engine = ResilientCP2DataEngine()
    result = asyncio.run(engine.cycle(NOW))

    assert len(calls) == 3
    assert result.unique_stock_count == 728
    assert not ResilientCP2DataEngine._coverage_complete(result)
