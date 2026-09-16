import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from pullback_engine.cp2 import (
    CP2DataEngine,
    EXPECTED_STOCKS,
    EXPECTED_STOCKS_PER_SHARD,
    STOCK_SHARDS,
    TransportResult,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 9, 16, 12, 54, tzinfo=IST)


def candle(ts="2026-09-16 12:54:00 IST", close=100):
    return {
        "timestamp": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 100,
    }


def stock(symbol, close=100, ts="2026-09-16 12:54:00 IST"):
    return {
        "symbol": symbol,
        "security_id": "1000",
        "previous_close": close - 1,
        "today_open": close,
        "candles_1m": [candle(ts, close)],
    }


def payload(symbols):
    return {
        "service": "PSYGRID",
        "schema_version": "4.0",
        "status": "OK",
        "universe_size": 450,
        "stock_count": len(symbols),
        "data_policy": "1M_OHLCV_PLUS_PREVIOUS_CLOSE_AND_TODAY_OPEN",
        "synthetic_candles": False,
        "stocks": {s: stock(s) for s in symbols},
    }


def nifty():
    return {
        "service": "PSYGRID",
        "schema_version": "4.0",
        "status": "OK",
        "symbol": "NIFTY",
        "security_id": "13",
        "exchange_segment": "IDX_I",
        "5m": [candle()],
    }


def run_engine(monkeypatch, responses, nifty_response):
    async def fake_fetch(self):
        return [*responses.values(), nifty_response]

    monkeypatch.setattr(CP2DataEngine, "_fetch_all", fake_fetch)
    engine = CP2DataEngine(
        endpoint_urls={k: k for k in responses},
        nifty_url="n",
    )
    return asyncio.run(engine.cycle(NOW))


def test_cp2_accepts_live_psygrid_v4_10x45(monkeypatch):
    responses = {}
    for idx, shard in enumerate(STOCK_SHARDS):
        symbols = [f"S{idx:02d}{n:02d}" for n in range(45)]
        responses[shard] = TransportResult(shard, shard, payload(symbols), None, 1.0)

    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", nifty(), None, 1.0))

    assert len(cycle.endpoints) == 10
    assert all(v.stock_count == EXPECTED_STOCKS_PER_SHARD for v in cycle.endpoints.values())
    assert cycle.unique_stock_count == EXPECTED_STOCKS
    assert cycle.healthy_stock_count == EXPECTED_STOCKS
    assert cycle.nifty_error is None

    sample = cycle.stocks["S0000"]
    assert sample.security_id == "1000"
    assert sample.previous_close == 99.0
    assert sample.today_open == 100.0
    assert len(sample.candles_1m) == 1


def test_cp2_reads_candles_1m_not_legacy_1m(monkeypatch):
    p = payload(["ABC"])
    cycle = run_engine(
        monkeypatch,
        {"a": TransportResult("a", "a", p, None, 1)},
        TransportResult("NIFTY", "n", nifty(), None, 1),
    )
    stock_data = cycle.stocks["ABC"]
    assert stock_data.healthy
    assert stock_data.candles_1m[0].close == 100
    assert "missing candles_1m candle list" not in stock_data.errors


def test_endpoint_failure_does_not_stop_other_endpoints(monkeypatch):
    responses = {
        "a": TransportResult("a", "a", payload([f"A{i}" for i in range(45)]), None, 1),
        "b": TransportResult("b", "b", None, "ConnectionError: down", 1),
    }
    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", nifty(), None, 1))
    assert cycle.endpoints["b"].error
    assert cycle.endpoints["a"].stock_count == 45
    assert cycle.unique_stock_count == 45


def test_stock_failure_does_not_stop_sibling_stock(monkeypatch):
    good = stock("GOOD")
    bad = stock("BAD")
    bad["candles_1m"] = [dict(candle(), high=90)]
    p = {"stocks": {"GOOD": good, "BAD": bad}}
    cycle = run_engine(
        monkeypatch,
        {"a": TransportResult("a", "a", p, None, 1)},
        TransportResult("NIFTY", "n", nifty(), None, 1),
    )
    assert cycle.stocks["GOOD"].healthy
    assert not cycle.stocks["BAD"].healthy
    assert cycle.stocks["BAD"].errors


def test_nifty_failure_does_not_stop_stock_ingestion(monkeypatch):
    responses = {"a": TransportResult("a", "a", payload([f"A{i}" for i in range(45)]), None, 1)}
    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", None, "timeout", 1))
    assert cycle.unique_stock_count == 45
    assert cycle.nifty_error == "timeout"


def test_nifty_schema_is_checked(monkeypatch):
    responses = {"a": TransportResult("a", "a", payload(["A"]), None, 1)}
    bad_nifty = TransportResult(
        "NIFTY", "n",
        {"symbol": "BANKNIFTY", "security_id": "25", "5m": []},
        None, 1,
    )
    cycle = run_engine(monkeypatch, responses, bad_nifty)
    assert cycle.unique_stock_count == 1
    assert cycle.nifty_error == "NIFTY symbol mismatch"


def test_shard_count_mismatch_isolated(monkeypatch):
    responses = {"a": TransportResult("a", "a", payload([f"A{i}" for i in range(44)]), None, 1)}
    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", nifty(), None, 1))
    assert cycle.endpoints["a"].error == "universe_count:44!=45"
    assert cycle.unique_stock_count == 44


def test_duplicate_symbol_across_endpoints_is_not_silently_accepted(monkeypatch):
    responses = {
        "a": TransportResult("a", "a", payload(["DUP"]), None, 1),
        "b": TransportResult("b", "b", payload(["DUP"]), None, 1),
    }
    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", nifty(), None, 1))
    assert not cycle.stocks["DUP"].healthy
    assert any("duplicate_symbol_across_endpoints" in e for e in cycle.stocks["DUP"].errors)


def test_stale_stock_isolated(monkeypatch):
    old = stock("OLD", ts="2026-09-16 11:55:00 IST")
    responses = {"a": TransportResult("a", "a", {"stocks": {"OLD": old}}, None, 1)}
    cycle = run_engine(monkeypatch, responses, TransportResult("NIFTY", "n", nifty(), None, 1))
    assert cycle.stocks["OLD"].stale
    assert not cycle.stocks["OLD"].healthy


def test_fresh_ltp_prevents_false_stale_on_sparse_trading_stock(monkeypatch):
    old = stock("ILLIQUID", ts="2026-09-16 11:50:00 IST")
    old["ltp_timestamp"] = "2026-09-16 12:54:30 IST"
    response = TransportResult("a", "a", {"stocks": {"ILLIQUID": old}}, None, 1)
    cycle = run_engine(monkeypatch, {"a": response}, TransportResult("NIFTY", "n", nifty(), None, 1))
    s = cycle.stocks["ILLIQUID"]
    assert s.feed_timestamp is not None
    assert not s.stale
    assert s.healthy


def test_engine_health_never_reports_global_stop_for_local_failures(monkeypatch):
    engine = CP2DataEngine(endpoint_urls={"a": "a"}, nifty_url="n")

    async def fake_fetch(self):
        return [
            TransportResult("a", "a", None, "boom", 1),
            TransportResult("NIFTY", "n", None, "boom", 1),
        ]

    monkeypatch.setattr(CP2DataEngine, "_fetch_all", fake_fetch)
    asyncio.run(engine.cycle(NOW))
    health = engine.health()
    assert health["engine"] == "RUNNING"
    assert health["unique_stocks"] == 0
