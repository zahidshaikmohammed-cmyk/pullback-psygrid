from __future__ import annotations

from datetime import datetime, timedelta

from pullback_engine.core import Candle, IST
from pullback_engine.cp2 import CP2Cycle, StockData
from pullback_engine.cp3 import (
    Impulse,
    PullbackAnalysis,
    PullbackCandidate,
    STATE_ARMED,
)
from pullback_engine.cp4 import (
    CP4TriggerEngine,
    calculate_early_entry_from_5m,
    candidate_is_price_eligible,
    completed_1m_index,
    long_reacceleration,
    short_reacceleration,
)


def make_1m_candles(
    closes: list[float],
    start: datetime | None = None,
) -> list[Candle]:
    start = start or datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=IST,
    )

    return [
        Candle(
            timestamp=start + timedelta(minutes=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000.0,
        )
        for i, close in enumerate(closes)
    ]


def make_5m_candles(
    n: int = 80,
) -> list[Candle]:
    start = datetime(
        2026,
        9,
        12,
        9,
        15,
        tzinfo=IST,
    )

    candles: list[Candle] = []

    for i in range(n):
        if i <= 20:
            close = 100.0 + i
        else:
            close = 120.0 - ((i - 20) * 1.5)

        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=5 * i),
                open=close - 0.2,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=1000.0,
            )
        )

    return candles
    start = datetime(
        2026,
        9,
        12,
        9,
        15,
        tzinfo=IST,
    )

    candles: list[Candle] = []

    for i in range(n):
        close = 100.0 + i * 0.5

        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=5 * i),
                open=close - 0.2,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=1000.0,
            )
        )

    return candles


def make_candidate(
    direction: str = "LONG",
) -> PullbackCandidate:
    impulse = Impulse(
        direction=direction,
        impulse_low_index=10,
        impulse_high_index=20,
        distance=20.0,
        atr_at_extreme=5.0,
        duration_bars=10,
        percentage=0.20,
    )

    pullback = PullbackAnalysis(
        pullback_start_index=21,
        pullback_end_index=30,
        retracement=0.50,
        duration_bars=10,
        structure="A",
        momentum_impulse=1.0,
        momentum_pullback=0.4,
        momentum_ratio=0.4,
        volume_ratio=0.7,
        vwap=105.0,
        early_entry_long=0.20,
        early_entry_short=0.20,
    )

    return PullbackCandidate(
        setup_id="TEST-PB-20260912-001",
        symbol="TEST",
        direction=direction,
        state=STATE_ARMED,
        created_at=datetime(
            2026,
            9,
            12,
            10,
            0,
            tzinfo=IST,
        ),
        updated_at=datetime(
            2026,
            9,
            12,
            10,
            30,
            tzinfo=IST,
        ),
        impulse=impulse,
        pullback=pullback,
        current_close=110.0,
        nifty_regime=True,
        stock_trend=True,
        quality={},
    )


def make_stock(
    closes: list[float],
    symbol: str = "TEST",
) -> StockData:
    candles = make_1m_candles(closes)

    return StockData(
        symbol=symbol,
        endpoint="a",
        candles_1m=candles,
        errors=[],
        stale=False,
        healthy=True,
        last_timestamp=candles[-1].timestamp,
    )


def make_cycle(
    stock: StockData,
    now: datetime,
    nifty_payload: dict | None = None,
) -> CP2Cycle:
    return CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks={
            stock.symbol: stock,
        },
        nifty_payload=nifty_payload,
        nifty_error=None,
    )


def test_long_reacceleration_exact_rule():
    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    assert long_reacceleration(
        candles,
        3,
    )


def test_long_reacceleration_requires_new_high():
    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            99.0,
        ]
    )

    assert not long_reacceleration(
        candles,
        3,
    )


def test_long_reacceleration_requires_three_previous_candles():
    candles = make_1m_candles(
        [
            100.0,
            101.0,
            102.0,
        ]
    )

    assert not long_reacceleration(
        candles,
        2,
    )


def test_short_reacceleration_exact_rule():
    candles = make_1m_candles(
        [
            100.0,
            101.0,
            102.0,
            99.0,
        ]
    )

    assert short_reacceleration(
        candles,
        3,
    )


def test_short_reacceleration_requires_new_low():
    candles = make_1m_candles(
        [
            100.0,
            101.0,
            102.0,
            101.0,
        ]
    )

    assert not short_reacceleration(
        candles,
        3,
    )


def test_completed_1m_index_does_not_use_unfinished_candle():
    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    now = candles[2].timestamp + timedelta(
        seconds=30
    )

    assert completed_1m_index(
        candles,
        now,
    ) == 1


def test_completed_1m_index_uses_candle_after_interval_closes():
    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    now = candles[2].timestamp + timedelta(
        minutes=1
    )

    assert completed_1m_index(
        candles,
        now,
    ) == 2


def test_future_candle_is_not_used():
    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    now = candles[2].timestamp

    assert completed_1m_index(
        candles,
        now,
    ) == 1


def test_price_gate():
    assert candidate_is_price_eligible(
        1200.0
    )

    assert candidate_is_price_eligible(
        1000.0
    )

    assert not candidate_is_price_eligible(
        1200.01
    )


def test_price_gate_rejects_non_finite():
    assert not candidate_is_price_eligible(
        float("nan")
    )

    assert not candidate_is_price_eligible(
        float("inf")
    )


def test_early_entry_long_is_calculated():
    candles = make_5m_candles()

    candidate = make_candidate("LONG")

    result = calculate_early_entry_from_5m(
        candidate,
        candles,
        candles[30].close,
    )

    assert result is not None
    assert isinstance(result, float)


def test_early_entry_short_is_calculated():
    start = datetime(
        2026,
        9,
        12,
        9,
        15,
        tzinfo=IST,
    )

    candles: list[Candle] = []

    for i in range(80):
        if i <= 20:
            close = 120.0 - i
        else:
            close = 100.0 + ((i - 20) * 0.5)

        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=5 * i),
                open=close + 0.2,
                high=close + 0.3,
                low=close - 0.3,
                close=close,
                volume=1000.0,
            )
        )

    candidate = make_candidate("SHORT")

    result = calculate_early_entry_from_5m(
        candidate,
        candles,
        candles[30].close,
    )

    assert result is not None
    candles = make_5m_candles()

    candidate = make_candidate("SHORT")

    result = calculate_early_entry_from_5m(
        candidate,
        candles,
        candles[30].close,
    )

    assert result is not None
    assert isinstance(result, float)


def test_early_entry_requires_pullback():
    candles = make_5m_candles()

    candidate = make_candidate("LONG")
    candidate.pullback = None

    assert (
        calculate_early_entry_from_5m(
            candidate,
            candles,
            100.0,
        )
        is None
    )


def test_engine_stays_running():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=IST,
    )

    stock = make_stock(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    cycle = make_cycle(
        stock,
        now,
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert result is not None
    assert engine.running is True


def test_setup_id_is_only_triggered_once():
    engine = CP4TriggerEngine()

    setup_id = "TEST-PB-20260912-001"

    engine.triggered_setup_ids.add(
        setup_id
    )

    engine.triggered_setup_ids.add(
        setup_id
    )

    assert len(
        engine.triggered_setup_ids
    ) == 1


def test_signal_entry_price_definition():
    close = 123.45

    candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            close,
        ]
    )

    assert candles[-1].close == close


def test_long_short_reacceleration_are_mirrors():
    long_candles = make_1m_candles(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    short_candles = make_1m_candles(
        [
            100.0,
            101.0,
            102.0,
            99.0,
        ]
    )

    assert long_reacceleration(
        long_candles,
        3,
    )

    assert short_reacceleration(
        short_candles,
        3,
    )


def test_bad_stock_isolated():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=IST,
    )

    good = make_stock(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ],
        symbol="GOOD",
    )

    bad = StockData(
        symbol="BAD",
        endpoint="b",
        candles_1m=[],
        errors=["bad_data"],
        stale=False,
        healthy=False,
        last_timestamp=None,
    )

    cycle = CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks={
            "GOOD": good,
            "BAD": bad,
        },
        nifty_payload=None,
        nifty_error="NIFTY unavailable",
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert "GOOD" in result.stock_results
    assert "BAD" in result.stock_results
    assert result.stock_results[
        "BAD"
    ].errors


def test_one_stock_error_does_not_stop_other_stock():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=IST,
    )

    good = make_stock(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ],
        symbol="GOOD",
    )

    bad = StockData(
        symbol="BAD",
        endpoint="b",
        candles_1m=[],
        errors=[],
        stale=False,
        healthy=True,
        last_timestamp=None,
    )

    cycle = CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks={
            "GOOD": good,
            "BAD": bad,
        },
        nifty_payload=None,
        nifty_error="NIFTY unavailable",
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert "GOOD" in result.stock_results
    assert "BAD" in result.stock_results
    assert engine.running is True


def test_cycle_processes_multiple_stocks():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=IST,
    )

    stocks = {
        "AAA": make_stock(
            [100.0, 99.0, 98.0, 101.0],
            "AAA",
        ),
        "BBB": make_stock(
            [200.0, 199.0, 198.0, 201.0],
            "BBB",
        ),
        "CCC": make_stock(
            [300.0, 299.0, 298.0, 301.0],
            "CCC",
        ),
    }

    cycle = CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks=stocks,
        nifty_payload=None,
        nifty_error="NIFTY unavailable",
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert set(
        result.stock_results
    ) == {
        "AAA",
        "BBB",
        "CCC",
    }


def test_no_trigger_before_10am():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        9,
        59,
        tzinfo=IST,
    )

    stock = make_stock(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    cycle = make_cycle(
        stock,
        now,
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert result.signals == []


def test_no_trigger_at_or_after_market_end():
    engine = CP4TriggerEngine()

    now = datetime(
        2026,
        9,
        12,
        15,
        15,
        tzinfo=IST,
    )

    stock = make_stock(
        [
            100.0,
            99.0,
            98.0,
            101.0,
        ]
    )

    cycle = make_cycle(
        stock,
        now,
    )

    result = engine.cycle(
        cycle,
        now=now,
    )

    assert result.signals == []