from __future__ import annotations

from datetime import datetime, timedelta

import pullback_engine.cp3 as cp3
from pullback_engine.core import Candle, IST
from pullback_engine.cp3 import CP3PullbackHunter, Impulse


def candles_from_closes(closes: list[float]) -> list[Candle]:
    start = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    return [
        Candle(timestamp=start + timedelta(minutes=5 * i), open=x, high=x + 0.1, low=x - 0.1, close=x, volume=1000.0)
        for i, x in enumerate(closes)
    ]


def test_pullback_sequences_keep_one_leg_and_deeper_two_leg(monkeypatch):
    candles = candles_from_closes(list(range(100, 125)))
    impulse = Impulse("LONG", 2, 8, 18.0, 2.0, 6, 0.18)
    monkeypatch.setattr(cp3, "confirmed_pivots", lambda *args, **kwargs: ([8, 12], [2, 11, 16]))
    sequences = CP3PullbackHunter._pullback_sequences(candles, impulse, len(candles) - 1)
    assert sequences == [("ONE_LEG", 11), ("TWO_LEG", 16)]


def test_pullback_analysis_does_not_die_when_structure_classification_is_none(monkeypatch):
    candles = candles_from_closes([100, 102, 104, 106, 108, 110, 120, 117, 114, 111, 108, 109, 110])
    impulse = Impulse("LONG", 1, 6, 20.0, 5.0, 5, 0.20)
    monkeypatch.setattr(cp3, "canonical_structure", lambda *args, **kwargs: None)
    analysis = CP3PullbackHunter._pullback_analysis(candles, impulse, 9)
    assert analysis is not None
    assert analysis.structure is None
    assert 0.30 <= analysis.retracement <= 0.80


def test_one_leg_and_two_leg_setup_ids_are_distinct():
    hunter = CP3PullbackHunter()
    candles = candles_from_closes([100 + i for i in range(40)])
    impulse = Impulse("LONG", 5, 12, 10.0, 5.0, 7, 0.10)
    first = hunter._make_candidate("TEST", "LONG", candles, impulse, None, cp3.STATE_IMPULSE, 20, {}, [None] * len(candles), "ONE_LEG", 1)
    second = hunter._make_candidate("TEST", "LONG", candles, impulse, None, cp3.STATE_IMPULSE, 20, {}, [None] * len(candles), "TWO_LEG", 2)
    assert first.setup_id != second.setup_id
