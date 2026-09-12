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

NOW = datetime(
    2026,
    9,
    12,
    12,
    0,
    tzinfo=IST,
)


def candle(
    ts="2026-09-12T11:59:00+05:30",
    close=100,
):
    return {
        "timestamp": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 100,
    }


def payload(symbols):
    return {
        "universe_size": 450,
        "stock_count": len(symbols),
        "stocks": {
            s: {
                "1m": [candle()]
            }
            for s in symbols
        },
    }


def nifty():
    return {
        "symbol": "NIFTY",
        "security_id": "13",
        "exchange_segment": "IDX_I",
        "instrument": "INDEX",
        "5m": [candle()],
    }


def run_engine(
    monkeypatch,
    responses,
    nifty_response,
):
    async def fake_fetch(self):
        out = [
            responses[k]
            for k in responses
        ]

        out.append(nifty_response)

        return out

    monkeypatch.setattr(
        CP2DataEngine,
        "_fetch_all",
        fake_fetch,
    )

    engine = CP2DataEngine(
        endpoint_urls={
            k: k
            for k in responses
        },
        nifty_url="n",
    )

    return asyncio.run(
        engine.cycle(NOW)
    )


def test_cp2_accepts_exact_10x45_and_nifty(
    monkeypatch,
):
    responses = {}

    for idx, shard in enumerate(STOCK_SHARDS):

        symbols = [
            f"S{idx:02d}{n:02d}"
            for n in range(45)
        ]

        responses[shard] = TransportResult(
            shard,
            shard,
            payload(symbols),
            None,
            1.0,
        )

    nifty_result = TransportResult(
        "NIFTY",
        "n",
        nifty(),
        None,
        1.0,
    )

    cycle = run_engine(
        monkeypatch,
        responses,
        nifty_result,
    )

    assert len(cycle.endpoints) == 10

    assert all(
        v.stock_count
        == EXPECTED_STOCKS_PER_SHARD
        for v in cycle.endpoints.values()
    )

    assert (
        cycle.unique_stock_count
        == EXPECTED_STOCKS
    )

    assert (
        cycle.healthy_stock_count
        == EXPECTED_STOCKS
    )

    assert cycle.nifty_error is None


def test_endpoint_failure_does_not_stop_other_endpoints(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            payload([
                f"A{i}"
                for i in range(45)
            ]),
            None,
            1,
        ),

        "b": TransportResult(
            "b",
            "b",
            None,
            "ConnectionError: down",
            1,
        ),
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            nifty(),
            None,
            1,
        ),
    )

    assert cycle.endpoints["b"].error

    assert (
        cycle.endpoints["a"].stock_count
        == 45
    )

    assert cycle.unique_stock_count == 45


def test_stock_failure_does_not_stop_sibling_stock(
    monkeypatch,
):
    good = candle()

    bad = dict(
        good,
        high=90,
    )

    p = {
        "stocks": {
            "GOOD": {
                "1m": [good]
            },
            "BAD": {
                "1m": [bad]
            },
        }
    }

    responses = {
        "a": TransportResult(
            "a",
            "a",
            p,
            None,
            1,
        )
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            nifty(),
            None,
            1,
        ),
    )

    assert cycle.stocks["GOOD"].healthy

    assert not cycle.stocks["BAD"].healthy

    assert cycle.stocks["BAD"].errors


def test_nifty_failure_does_not_stop_stock_ingestion(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            payload([
                f"A{i}"
                for i in range(45)
            ]),
            None,
            1,
        )
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            None,
            "timeout",
            1,
        ),
    )

    assert cycle.unique_stock_count == 45

    assert cycle.nifty_error == "timeout"


def test_nifty_schema_is_checked(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            payload(["A"]),
            None,
            1,
        )
    }

    bad_nifty = TransportResult(
        "NIFTY",
        "n",
        {
            "symbol": "BANKNIFTY",
            "security_id": "25",
            "5m": [],
        },
        None,
        1,
    )

    cycle = run_engine(
        monkeypatch,
        responses,
        bad_nifty,
    )

    assert cycle.unique_stock_count == 1

    assert (
        cycle.nifty_error
        == "NIFTY symbol mismatch"
    )


def test_shard_count_mismatch_isolated(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            payload([
                f"A{i}"
                for i in range(44)
            ]),
            None,
            1,
        )
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            nifty(),
            None,
            1,
        ),
    )

    assert (
        cycle.endpoints["a"].error
        == "universe_count:44!=45"
    )

    assert cycle.unique_stock_count == 44


def test_duplicate_symbol_across_endpoints_is_not_silently_accepted(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            payload(["DUP"]),
            None,
            1,
        ),

        "b": TransportResult(
            "b",
            "b",
            payload(["DUP"]),
            None,
            1,
        ),
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            nifty(),
            None,
            1,
        ),
    )

    assert not cycle.stocks["DUP"].healthy

    assert any(
        "duplicate_symbol_across_endpoints"
        in e
        for e in cycle.stocks["DUP"].errors
    )


def test_stale_stock_isolated(
    monkeypatch,
):
    old = candle(
        "2026-09-12T11:55:00+05:30"
    )

    responses = {
        "a": TransportResult(
            "a",
            "a",
            {
                "stocks": {
                    "OLD": {
                        "1m": [old]
                    }
                }
            },
            None,
            1,
        )
    }

    cycle = run_engine(
        monkeypatch,
        responses,
        TransportResult(
            "NIFTY",
            "n",
            nifty(),
            None,
            1,
        ),
    )

    assert cycle.stocks["OLD"].stale

    assert not cycle.stocks["OLD"].healthy


def test_engine_health_never_reports_global_stop_for_local_failures(
    monkeypatch,
):
    responses = {
        "a": TransportResult(
            "a",
            "a",
            None,
            "boom",
            1,
        )
    }

    engine = CP2DataEngine(
        endpoint_urls={"a": "a"},
        nifty_url="n",
    )

    async def fake_fetch(self):
        return [
            responses["a"],
            TransportResult(
                "NIFTY",
                "n",
                None,
                "boom",
                1,
            ),
        ]

    monkeypatch.setattr(
        CP2DataEngine,
        "_fetch_all",
        fake_fetch,
    )

    asyncio.run(
        engine.cycle(NOW)
    )

    health = engine.health()

    assert health["engine"] == "RUNNING"

    assert health["unique_stocks"] == 0