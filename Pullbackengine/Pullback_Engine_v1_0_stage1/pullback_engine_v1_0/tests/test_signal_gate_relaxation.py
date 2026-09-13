from datetime import datetime, timedelta

from pullback_engine.core import Candle, IST
from pullback_engine.cp2 import CP2DataEngine, StockData
from pullback_engine.cp3 import Impulse, PullbackAnalysis, PullbackCandidate, STATE_ARMED
from pullback_engine.cp4 import CP4TriggerEngine, calculate_early_entry_from_5m


def make_1m(values):
    start = datetime(2026, 9, 12, 10, 0, tzinfo=IST)
    return [
        Candle(start + timedelta(minutes=i), v, v, v, v, 1000.0)
        for i, v in enumerate(values)
    ]


def make_5m():
    start = datetime(2026, 9, 12, 9, 15, tzinfo=IST)
    rows = []
    for i in range(40):
        if i <= 20:
            close = 100.0 + i
        elif i <= 30:
            close = 120.0 - 2.0 * (i - 20)
        else:
            close = 100.0 + 0.5 * (i - 30)
        rows.append(
            Candle(
                start + timedelta(minutes=5 * i),
                close,
                close + 0.5,
                close - 0.5,
                close,
                1000.0,
            )
        )
    return rows


def make_candidate():
    return PullbackCandidate(
        setup_id="TEST-PB-20260912-001",
        symbol="TEST",
        direction="LONG",
        state=STATE_ARMED,
        created_at=datetime(2026, 9, 12, 10, 0, tzinfo=IST),
        updated_at=datetime(2026, 9, 12, 10, 30, tzinfo=IST),
        impulse=Impulse("LONG", 10, 20, 20.0, 5.0, 10, 0.20),
        pullback=PullbackAnalysis(21, 30, 0.50, 10, "A", 1.0, 0.4, 0.4, 0.7, 105.0, 0.20, 0.20),
        current_close=110.0,
        nifty_regime=False,
        stock_trend=True,
        quality={},
    )


def test_trigger_does_not_require_nifty_regime_or_ep_threshold():
    engine = CP4TriggerEngine()
    stock = StockData(
        "TEST",
        "a",
        make_1m([100.0, 99.0, 98.0, 110.0]),
        [],
        False,
        True,
        datetime(2026, 9, 12, 10, 3, tzinfo=IST),
    )
    candidate = make_candidate()
    candles_5m = make_5m()

    ep = calculate_early_entry_from_5m(candidate, candles_5m, 110.0)
    assert ep is not None and ep > 0.45

    signal = engine._evaluate_candidate(
        candidate,
        stock,
        candles_5m,
        3,
        datetime(2026, 9, 12, 10, 4, tzinfo=IST),
    )

    assert signal is not None


def test_cp2_keeps_current_stock_usable_when_one_historical_row_is_invalid():
    engine = CP2DataEngine()
    rows = [
        {"timestamp": "2026-09-12T09:15:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"timestamp": "2026-09-12T09:16:00+05:30", "open": 100, "high": 90, "low": 99, "close": 100, "volume": 1000},
        {"timestamp": "2026-09-12T09:17:00+05:30", "open": 101, "high": 102, "low": 100, "close": 101, "volume": 1000},
    ]
    stock = engine._stock(
        "a",
        "TEST",
        {"1m": rows},
        datetime(2026, 9, 12, 9, 18, tzinfo=IST),
    )

    assert stock.errors
    assert stock.healthy is True
    assert len(stock.candles_1m) == 2
