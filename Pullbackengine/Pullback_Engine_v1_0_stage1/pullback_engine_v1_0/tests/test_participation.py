from datetime import datetime, timedelta

from pullback_engine.core import Candle, IST
from pullback_engine.participation import (
    detect_early_participation_return,
)


def candles(volumes, closes=None, short=False):
    closes = closes or [100.0 + i * 0.1 for i in range(len(volumes))]
    start = datetime(2026, 9, 12, 10, 0, tzinfo=IST)
    return [
        Candle(
            start + timedelta(minutes=5 * i),
            closes[i] + 0.1 if short else closes[i] - 0.1,
            closes[i] + 0.2,
            closes[i] - 0.2,
            closes[i],
            float(volumes[i]),
        )
        for i in range(len(volumes))
    ]


def test_early_participation_return_is_detected_without_full_momentum():
    # Impulse baseline = 1000. Pullback participation fades to 500,
    # then returns modestly to 750 with an upward price response.
    c = candles(
        [1000, 1000, 1000, 500, 600, 750],
        [100, 101, 102, 101, 101.05, 101.20],
    )
    result = detect_early_participation_return(
        c,
        pullback_start=3,
        pullback_end=5,
        impulse_start=0,
        impulse_end=2,
        direction="LONG",
    )
    assert result.detected
    assert result.status == "CONFIRMED"
    assert result.recovery_fraction > 0.15


def test_short_return_is_detected_symmetrically():
    c = candles(
        [1000, 1000, 1000, 500, 600, 750],
        [100, 99, 98, 99, 98.95, 98.80],
        short=True,
    )
    result = detect_early_participation_return(
        c,
        pullback_start=3,
        pullback_end=5,
        impulse_start=0,
        impulse_end=2,
        direction="SHORT",
    )
    assert result.detected


def test_volume_spike_without_directional_price_response_is_not_return():
    c = candles(
        [1000, 1000, 1000, 500, 600, 900],
        [100, 101, 102, 101, 101.05, 100.90],
    )
    result = detect_early_participation_return(
        c,
        pullback_start=3,
        pullback_end=5,
        impulse_start=0,
        impulse_end=2,
        direction="LONG",
    )
    assert not result.detected
    assert result.status == "NOT_DETECTED"


def test_missing_baseline_does_not_create_signal():
    c = candles([0, 0, 0, 500, 600, 750])
    result = detect_early_participation_return(
        c,
        pullback_start=3,
        pullback_end=5,
        impulse_start=0,
        impulse_end=2,
        direction="LONG",
    )
    assert result.status == "INSUFFICIENT_DATA"
    assert not result.detected
