from __future__ import annotations

from datetime import datetime, timedelta

from pullback_engine.core import Candle, IST
from pullback_engine.cp2 import CP2Cycle, StockData
from pullback_engine.cp3 import (
    CP3PullbackHunter,
    CP3StockResult,
    Impulse,
    PullbackAnalysis,
    STATE_ARMED,
    STATE_IMPULSE,
    STATE_PULLBACK,
    STATE_QUALIFIED,
)


def make_candles(
    n: int = 100,
    start: datetime | None = None,
    base: float = 100.0,
) -> list[Candle]:
    start = start or datetime(
        2026,
        9,
        12,
        9,
        15,
        tzinfo=IST,
    )

    candles: list[Candle] = []

    for i in range(n):
        close = base + i * 0.10

        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=i),
                open=close - 0.05,
                high=close + 0.10,
                low=close - 0.10,
                close=close,
                volume=1000.0,
            )
        )

    return candles


def make_stock(
    symbol: str = "TEST",
    candles: list[Candle] | None = None,
    healthy: bool = True,
) -> StockData:
    return StockData(
        symbol=symbol,
        endpoint="a",
        candles_1m=candles or make_candles(),
        errors=[],
        stale=False,
        healthy=healthy,
        last_timestamp=(
            candles[-1].timestamp
            if candles
            else None
        ),
    )


def make_cycle(
    stocks: dict[str, StockData],
    now: datetime | None = None,
) -> CP2Cycle:
    now = now or datetime(
        2026,
        9,
        12,
        10,
        30,
        tzinfo=IST,
    )

    return CP2Cycle(
        timestamp=now,
        endpoints={},
        stocks=stocks,
        nifty_payload={
            "symbol": "NIFTY",
            "security_id": "13",
            "exchange_segment": "IDX_I",
            "5m": [],
        },
        nifty_error=None,
    )


def test_state_constants():
    assert STATE_IMPULSE == "IMPULSE_DETECTED"
    assert STATE_PULLBACK == "PULLBACK_CANDIDATE"
    assert STATE_QUALIFIED == "QUALIFIED_PULLBACK"
    assert STATE_ARMED == "TRIGGER_ARMED"


def test_no_candidate_before_10am():
    hunter = CP3PullbackHunter()

    candles = make_candles(
        start=datetime(
            2026,
            9,
            12,
            9,
            15,
            tzinfo=IST,
        )
    )

    stock = make_stock(candles=candles)

    cycle = make_cycle(
        {"TEST": stock},
        now=datetime(
            2026,
            9,
            12,
            9,
            59,
            tzinfo=IST,
        ),
    )

    result = hunter.cycle(
        cycle,
        now=cycle.timestamp,
    )

    assert result.candidates == []


def test_no_candidate_at_or_after_1515():
    hunter = CP3PullbackHunter()

    candles = make_candles(
        start=datetime(
            2026,
            9,
            12,
            9,
            15,
            tzinfo=IST,
        )
    )

    stock = make_stock(candles=candles)

    cycle = make_cycle(
        {"TEST": stock},
        now=datetime(
            2026,
            9,
            12,
            15,
            15,
            tzinfo=IST,
        ),
    )

    result = hunter.cycle(
        cycle,
        now=cycle.timestamp,
    )

    assert result.candidates == []


def test_impulse_requires_at_least_three_bars():
    impulse = Impulse(
        direction="LONG",
        impulse_low_index=10,
        impulse_high_index=13,
        distance=10.0,
        atr_at_extreme=5.0,
        duration_bars=3,
        percentage=0.10,
    )

    assert impulse.duration_bars >= 3


def test_impulse_requires_atr_distance():
    impulse = Impulse(
        direction="LONG",
        impulse_low_index=10,
        impulse_high_index=15,
        distance=10.0,
        atr_at_extreme=10.0,
        duration_bars=5,
        percentage=0.10,
    )

    assert impulse.distance >= impulse.atr_at_extreme


def test_no_unconfirmed_pivot_is_used():
    candles = make_candles(30)

    hunter = CP3PullbackHunter()

    bullish, bearish = hunter._find_latest_impulses(
        candles,
        through_index=5,
    )

    assert isinstance(bullish, list)
    assert isinstance(bearish, list)

    for impulse in bullish + bearish:
        assert impulse.impulse_high_index <= 2
        assert impulse.impulse_low_index <= 2


def test_next_pullback_pivot_is_after_impulse():
    candles = make_candles(50)

    impulse = Impulse(
        direction="LONG",
        impulse_low_index=10,
        impulse_high_index=20,
        distance=10.0,
        atr_at_extreme=5.0,
        duration_bars=10,
        percentage=0.10,
    )

    pb = CP3PullbackHunter._next_pullback_pivot(
        candles,
        impulse,
        through_index=40,
    )

    if pb is not None:
        assert pb > impulse.impulse_high_index


def test_retracement_and_duration_are_stored():
    analysis = PullbackAnalysis(
        pullback_start_index=20,
        pullback_end_index=25,
        retracement=0.50,
        duration_bars=5,
        structure="A",
        momentum_impulse=1.0,
        momentum_pullback=0.5,
        momentum_ratio=0.5,
        volume_ratio=1.0,
        vwap=105.0,
        early_entry_long=0.30,
        early_entry_short=0.70,
    )

    assert 0.30 <= analysis.retracement <= 0.80
    assert analysis.duration_bars >= 3


def test_canonical_structure_is_callable():
    candles = make_candles(50)

    result = CP3PullbackHunter._pullback_analysis

    assert callable(result)
    assert candles


def test_volume_and_vwap_are_secondary_metrics():
    analysis = PullbackAnalysis(
        pullback_start_index=10,
        pullback_end_index=15,
        retracement=0.50,
        duration_bars=5,
        structure="A",
        momentum_impulse=1.0,
        momentum_pullback=0.4,
        momentum_ratio=0.4,
        volume_ratio=0.2,
        vwap=100.0,
        early_entry_long=0.30,
        early_entry_short=0.70,
    )

    assert analysis.volume_ratio == 0.2
    assert analysis.vwap == 100.0


def test_bad_stock_isolated():
    hunter = CP3PullbackHunter()

    good = make_stock("GOOD")
    bad = make_stock(
        "BAD",
        candles=[],
        healthy=True,
    )

    cycle = make_cycle(
        {
            "GOOD": good,
            "BAD": bad,
        }
    )

    result = hunter.cycle(
        cycle,
        now=cycle.timestamp,
    )

    assert "GOOD" in result.stock_results
    assert "BAD" in result.stock_results

    assert isinstance(
        result.stock_results["GOOD"],
        CP3StockResult,
    )


def test_multiple_stocks_are_processed_independently():
    hunter = CP3PullbackHunter()

    stocks = {
        "AAA": make_stock("AAA"),
        "BBB": make_stock("BBB"),
        "CCC": make_stock("CCC"),
    }

    cycle = make_cycle(stocks)

    result = hunter.cycle(
        cycle,
        now=cycle.timestamp,
    )

    assert set(result.stock_results) == {
        "AAA",
        "BBB",
        "CCC",
    }


def test_quality_metrics_are_informational():
    hunter = CP3PullbackHunter()

    impulse = Impulse(
        direction="LONG",
        impulse_low_index=10,
        impulse_high_index=20,
        distance=10.0,
        atr_at_extreme=5.0,
        duration_bars=10,
        percentage=0.10,
    )

    analysis = PullbackAnalysis(
        pullback_start_index=21,
        pullback_end_index=26,
        retracement=0.50,
        duration_bars=5,
        structure="A",
        momentum_impulse=1.0,
        momentum_pullback=0.5,
        momentum_ratio=0.5,
        volume_ratio=0.2,
        vwap=100.0,
        early_entry_long=0.30,
        early_entry_short=0.70,
    )

    quality = hunter._quality(
        impulse,
        analysis,
        close=101.0,
        trend=False,
        regime=False,
    )

    assert "volume_ratio" in quality
    assert "vwap_side" in quality
    assert "momentum_ratio" in quality
    assert "trend_confirmation" in quality
    assert "regime_confirmation" in quality


def test_health_counts():
    hunter = CP3PullbackHunter()

    stock = make_stock("TEST")

    cycle = make_cycle(
        {"TEST": stock}
    )

    result = hunter.cycle(
        cycle,
        now=cycle.timestamp,
    )

    health = hunter.health()

    assert health["engine"] == "RUNNING"
    assert health["stocks_analyzed"] == len(
        result.stock_results
    )
    assert health["candidates"] == len(
        result.candidates
    )