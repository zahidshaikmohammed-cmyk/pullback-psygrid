import asyncio
from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.cp2 import CP2Cycle, EndpointData, StockData, STOCK_SHARDS
from pullback_engine.cp2_resilient import ResilientCP2DataEngine

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=IST)


def cycle_with_count(count: int) -> CP2Cycle:
    remaining = count
    endpoints = {}
    for shard in STOCK_SHARDS:
        shard_count = min(45, remaining)
        remaining -= shard_count
        endpoints[shard] = EndpointData(
            name=shard,
            url=shard,
            stock_count=shard_count,
            symbols=[],
            stocks={},
            error=None if shard_count == 45 else "partial",
            healthy=shard_count == 45,
        )

    if count == 450:
        for endpoint in endpoints.values():
            endpoint.error = None
            endpoint.healthy = True

    stocks = {
        f"S{i:03d}": StockData(
            symbol=f"S{i:03d}",
            endpoint="a",
            healthy=True,
        )
        for i in range(count)
    }
    return CP2Cycle(
        timestamp=NOW,
        endpoints=endpoints,
        stocks=stocks,
        nifty_payload={"symbol": "NIFTY"},
        nifty_error=None,
    )


def test_resilient_cp2_retries_incomplete_universe(monkeypatch):
    calls = []
    cycles = [cycle_with_count(328), cycle_with_count(328), cycle_with_count(450)]

    async def fake_cycle(self, now=None):
        calls.append(1)
        return cycles.pop(0)

    monkeypatch.setattr("pullback_engine.cp2.CP2DataEngine.cycle", fake_cycle)

    engine = ResilientCP2DataEngine()
    result = asyncio.run(engine.cycle(NOW))

    assert len(calls) == 3
    assert result.unique_stock_count == 450
    assert ResilientCP2DataEngine._coverage_complete(result)


def test_resilient_cp2_returns_last_snapshot_after_retry_budget(monkeypatch):
    calls = []

    async def fake_cycle(self, now=None):
        calls.append(1)
        return cycle_with_count(328)

    monkeypatch.setattr("pullback_engine.cp2.CP2DataEngine.cycle", fake_cycle)

    engine = ResilientCP2DataEngine()
    result = asyncio.run(engine.cycle(NOW))

    assert len(calls) == 3
    assert result.unique_stock_count == 328
    assert not ResilientCP2DataEngine._coverage_complete(result)
