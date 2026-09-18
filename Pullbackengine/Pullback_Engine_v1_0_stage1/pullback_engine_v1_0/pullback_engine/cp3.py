from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from .core import (
    Candle, HUNT_START, IST, MARKET_END, PRICE_MAX,
    aggregate_5m, atr_wilder, canonical_structure, confirmed_pivots,
    ema, momentum_metrics, retracement, session_vwap, volume_ratio,
)
from .cp2 import CP2Cycle, StockData

STATE_IDLE = "IDLE"
STATE_IMPULSE = "IMPULSE_DETECTED"
STATE_PULLBACK = "PULLBACK_CANDIDATE"
STATE_QUALIFIED = "QUALIFIED_PULLBACK"
STATE_ARMED = "TRIGGER_ARMED"

@dataclass(frozen=True)
class Impulse:
    direction: str
    impulse_low_index: int
    impulse_high_index: int
    distance: float
    atr_at_extreme: float
    duration_bars: int
    percentage: float

@dataclass(frozen=True)
class PullbackAnalysis:
    pullback_start_index: int
    pullback_end_index: int
    retracement: float
    duration_bars: int
    structure: str | None
    momentum_impulse: float | None
    momentum_pullback: float | None
    momentum_ratio: float | None
    volume_ratio: float | None
    vwap: float | None
    early_entry_long: float | None
    early_entry_short: float | None

@dataclass
class PullbackCandidate:
    setup_id: str
    symbol: str
    direction: str
    state: str
    created_at: datetime
    updated_at: datetime
    impulse: Impulse
    pullback: PullbackAnalysis | None
    current_close: float
    nifty_regime: bool | None
    stock_trend: bool | None
    quality: dict[str, float | None] = field(default_factory=dict)
    pullback_type: str = "ONE_LEG"

@dataclass
class CP3StockResult:
    symbol: str
    candles_5m: list[Candle]
    candidates: list[PullbackCandidate]
    errors: list[str] = field(default_factory=list)

@dataclass
class CP3Cycle:
    timestamp: datetime
    stock_results: dict[str, CP3StockResult]
    nifty_bullish: dict[datetime, bool]
    nifty_bearish: dict[datetime, bool]

    @property
    def candidates(self) -> list[PullbackCandidate]:
        return [c for r in self.stock_results.values() for c in r.candidates]

    @property
    def armed_candidates(self) -> list[PullbackCandidate]:
        return [c for c in self.candidates if c.state == STATE_ARMED]

class CP3PullbackHunter:
    """CP3: causal structural pullback hunter.

    The hunter evaluates the *current* impulse/pullback episode rather than
    recycling every historical pullback. Pullback depth is adaptive to impulse
    strength and is confirmed by duration, momentum, volume and swing structure.
    """

    MIN_STRUCTURAL_SCORE = 62.0

    def __init__(self) -> None:
        self.running = True
        self.last_cycle: CP3Cycle | None = None

    @staticmethod
    def _nifty_regime_maps(payload: Mapping[str, Any] | None) -> tuple[dict[datetime, bool], dict[datetime, bool]]:
        if not payload or not isinstance(payload.get("5m"), list):
            return {}, {}
        from .core import validate_candles, nifty_regime
        candles, _ = validate_candles(payload["5m"])
        if not candles:
            return {}, {}
        bull, bear = nifty_regime(candles)
        return (
            {c.timestamp: bool(v) for c, v in zip(candles, bull) if v is not None},
            {c.timestamp: bool(v) for c, v in zip(candles, bear) if v is not None},
        )

    @staticmethod
    def _trend_map(candles: Sequence[Candle]) -> tuple[list[bool | None], list[bool | None]]:
        closes = [c.close for c in candles]
        e20, e60 = ema(closes, 20), ema(closes, 60)
        bull: list[bool | None] = [None] * len(candles)
        bear: list[bool | None] = [None] * len(candles)
        for i, c in enumerate(candles):
            if i < 5 or e20[i] is None or e60[i] is None or e20[i-5] is None:
                continue
            bull[i] = c.close > e20[i] and e20[i] > e60[i] and e20[i] > e20[i-5]
            bear[i] = c.close < e20[i] and e20[i] < e60[i] and e20[i] < e20[i-5]
        return bull, bear

    @staticmethod
    def _adaptive_depth(impulse: Impulse) -> tuple[float, float, float]:
        """Return (minimum, ideal, maximum) retracement for this impulse.

        Strong/displaced impulses normally correct less; weak impulses need a
        deeper correction before the setup becomes structurally meaningful.
        The bounds move continuously with impulse ATR strength instead of
        applying one 30-80% band to every market condition.
        """
        strength = max(0.5, impulse.distance / max(impulse.atr_at_extreme, 1e-9))
        ideal = max(0.42, min(0.62, 0.58 - 0.045 * (strength - 2.0)))
        spread = max(0.12, min(0.22, 0.20 - 0.012 * max(0.0, strength - 2.0)))
        return max(0.18, ideal - spread), ideal, min(0.82, ideal + spread)

    @staticmethod
    def _structure_quality(
        candles: Sequence[Candle],
        impulse: Impulse,
        pullback: PullbackAnalysis,
    ) -> tuple[float, str]:
        """Score the correction's behaviour, not just its depth.

        100 means the observed correction is strongly consistent with a
        continuation pullback; low values indicate a reversal-like correction.
        """
        lo, ideal, hi = CP3PullbackHunter._adaptive_depth(impulse)
        r = pullback.retracement
        depth_score = max(0.0, 1.0 - abs(r - ideal) / max(ideal - lo, hi - ideal)) * 20.0

        duration_ratio = pullback.duration_bars / max(1, impulse.duration_bars)
        duration_score = max(0.0, min(1.0, 1.0 - max(0.0, duration_ratio - 0.5) / 2.0)) * 15.0

        mr = pullback.momentum_ratio
        if mr is None or not math.isfinite(mr):
            momentum_score = 0.0
        else:
            momentum_score = max(0.0, min(1.0, 1.0 - mr / 1.15)) * 20.0

        vr = pullback.volume_ratio
        if vr is None or not math.isfinite(vr):
            volume_score = 5.0
        else:
            volume_score = max(0.0, min(1.0, 1.25 - vr)) * 10.0

        structure_score = 15.0 if pullback.structure is not None else 0.0

        # A correction that travels faster than the impulse or has momentum
        # stronger than the impulse is treated as reversal risk.
        reversal_risk = duration_ratio > 2.5 or (mr is not None and math.isfinite(mr) and mr > 1.15)
        if reversal_risk:
            return max(0.0, depth_score + duration_score + momentum_score + volume_score + structure_score - 25.0), "REVERSAL_RISK"

        total = depth_score + duration_score + momentum_score + volume_score + structure_score
        if r < lo:
            kind = "SHALLOW_CORRECTION"
        elif r > hi:
            kind = "DEEP_CORRECTION"
        elif pullback.pullback_end_index - pullback.pullback_start_index > impulse.duration_bars:
            kind = "TWO_LEG_CORRECTION"
        else:
            kind = "NORMAL_CORRECTION"
        return total, kind

    @staticmethod
    def _quality(impulse: Impulse, pullback: PullbackAnalysis, close: float, trend: bool | None, regime: bool | None) -> dict[str, float | None]:
        score, classification = CP3PullbackHunter._structure_quality([], impulse, pullback)
        r = pullback.retracement
        lo, ideal, hi = CP3PullbackHunter._adaptive_depth(impulse)
        return {
            "structural_score": score,
            "structural_classification": classification,
            "adaptive_min_retracement": lo,
            "adaptive_ideal_retracement": ideal,
            "adaptive_max_retracement": hi,
            "impulse_atr_multiple": impulse.distance / impulse.atr_at_extreme if impulse.atr_at_extreme > 0 else None,
            "impulse_percentage": impulse.percentage,
            "retracement_depth_score": max(0.0, 1.0 - abs(r - ideal) / max(ideal - lo, hi - ideal)) if math.isfinite(r) else None,
            "duration_quality": 1.0 / (1.0 + max(0, pullback.duration_bars - 3) / 10.0),
            "momentum_ratio": pullback.momentum_ratio,
            "volume_ratio": pullback.volume_ratio,
            "vwap_side": (1.0 if (pullback.vwap is not None and ((close > pullback.vwap) if impulse.direction == "LONG" else (close < pullback.vwap))) else 0.0 if pullback.vwap is not None else None),
            "trend_confirmation": 1.0 if trend is True else 0.0 if trend is False else None,
            "regime_confirmation": 1.0 if regime is True else 0.0 if regime is False else None,
        }

    @staticmethod
    def _make_candidate(symbol: str, direction: str, candles: Sequence[Candle], impulse: Impulse, pullback: PullbackAnalysis | None, state: str, current_index: int, nifty_map: Mapping[datetime, bool], trend_map: Sequence[bool | None], pullback_type: str = "ONE_LEG", sequence: int = 1) -> PullbackCandidate:
        now = candles[current_index].timestamp
        regime, trend = nifty_map.get(now), trend_map[current_index]
        if pullback is None:
            setup_id = f"{symbol}-PB-{candles[impulse.impulse_high_index].timestamp.astimezone(IST):%Y%m%d-%H%M}-{impulse.impulse_low_index}-{impulse.impulse_high_index}-IMPULSE"
        else:
            setup_id = (
                f"{symbol}-PB-{candles[impulse.impulse_high_index].timestamp.astimezone(IST):%Y%m%d-%H%M}-"
                f"{impulse.impulse_low_index}-{impulse.impulse_high_index}-{pullback.pullback_end_index}-{pullback_type}"
            )
        quality = CP3PullbackHunter._quality(impulse, pullback, candles[current_index].close, trend, regime) if pullback else {}
        return PullbackCandidate(symbol=symbol, setup_id=setup_id, direction=direction, state=state, created_at=candles[impulse.impulse_low_index].timestamp, updated_at=now, impulse=impulse, pullback=pullback, current_close=candles[current_index].close, nifty_regime=regime, stock_trend=trend, quality=quality, pullback_type=pullback_type)

    @staticmethod
    def _find_latest_impulses(candles: Sequence[Candle], through_index: int) -> tuple[list[Impulse], list[Impulse]]:
        highs, lows = confirmed_pivots(candles, through_index=through_index)
        atrs = atr_wilder(candles, 14)
        bullish: list[Impulse] = []
        bearish: list[Impulse] = []
        for sl in lows:
            for sh in highs:
                if sh <= sl or sh > through_index - 3 or atrs[sh] is None or not math.isfinite(atrs[sh]):
                    continue
                d, n = candles[sh].high - candles[sl].low, sh - sl
                if n >= 3 and d >= atrs[sh]:
                    bullish.append(Impulse("LONG", sl, sh, d, atrs[sh], n, d / candles[sl].low if candles[sl].low > 0 else math.nan))
        for sh in highs:
            for sl in lows:
                if sl <= sh or sl > through_index - 3 or atrs[sl] is None or not math.isfinite(atrs[sl]):
                    continue
                d, n = candles[sh].high - candles[sl].low, sl - sh
                if n >= 3 and d >= atrs[sl]:
                    bearish.append(Impulse("SHORT", sh, sl, d, atrs[sl], n, d / candles[sl].low if candles[sl].low > 0 else math.nan))
        # Only the most recent confirmed directional leg is actionable. Older
        # impulses remain historical context but cannot generate new signals.
        latest_bull = max(bullish, key=lambda x: x.impulse_high_index, default=None)
        latest_bear = max(bearish, key=lambda x: x.impulse_high_index, default=None)
        return ([latest_bull] if latest_bull else []), ([latest_bear] if latest_bear else [])

    @staticmethod
    def _pullback_sequences(candles: Sequence[Candle], impulse: Impulse, through_index: int) -> list[tuple[str, int]]:
        highs, lows = confirmed_pivots(candles, through_index=through_index)
        origin = impulse.impulse_high_index if impulse.direction == "LONG" else impulse.impulse_low_index
        counter = [p for p in (lows if impulse.direction == "LONG" else highs) if origin < p <= through_index - 3]
        separator = [p for p in (highs if impulse.direction == "LONG" else lows) if origin < p <= through_index - 3]
        if not counter:
            return []
        first = counter[0]
        out: list[tuple[str, int]] = [("ONE_LEG", first)]
        for endpoint in counter[1:]:
            if any(first < p < endpoint for p in separator):
                extends = candles[endpoint].low < candles[first].low if impulse.direction == "LONG" else candles[endpoint].high > candles[first].high
                if extends:
                    out.append(("TWO_LEG", endpoint))
        return out

    @staticmethod
    def _next_pullback_pivot(candles: Sequence[Candle], impulse: Impulse, through_index: int) -> int | None:
        seq = CP3PullbackHunter._pullback_sequences(candles, impulse, through_index)
        return seq[0][1] if seq else None

    @staticmethod
    def _pullback_analysis(candles: Sequence[Candle], impulse: Impulse, pb: int) -> PullbackAnalysis | None:
        origin = impulse.impulse_high_index if impulse.direction == "LONG" else impulse.impulse_low_index
        start, duration = origin + 1, pb - origin
        if duration < 3:
            return None
        extreme = candles[pb].low if impulse.direction == "LONG" else candles[pb].high
        r = retracement(impulse.direction, candles[impulse.impulse_high_index].high, candles[impulse.impulse_low_index].low, extreme)
        lo, _ideal, hi = CP3PullbackHunter._adaptive_depth(impulse)
        if not math.isfinite(r) or not lo <= r <= hi:
            return None
        try:
            structure = canonical_structure(candles, start, pb, impulse.direction)
        except Exception:
            structure = None
        mi, mp, mr = momentum_metrics(candles, impulse.impulse_low_index, impulse.impulse_high_index, pb)
        vr = volume_ratio(candles, start, pb)
        vwaps = session_vwap(candles)
        vwap = vwaps[pb] if pb < len(vwaps) else None
        dl = candles[impulse.impulse_high_index].high - candles[pb].low
        ds = candles[pb].high - candles[impulse.impulse_low_index].low
        ep_long = (candles[pb].close - candles[pb].low) / dl if dl > 0 else math.nan
        ep_short = (candles[pb].high - candles[pb].close) / ds if ds > 0 else math.nan
        analysis = PullbackAnalysis(start, pb, r, duration, structure, mi, mp, mr, vr, vwap, ep_long, ep_short)
        return analysis

    def analyze_stock(self, stock: StockData, now: datetime, nifty_bull: Mapping[datetime, bool], nifty_bear: Mapping[datetime, bool]) -> CP3StockResult:
        if not stock.healthy:
            return CP3StockResult(stock.symbol, [], [], ["stock_unhealthy"])
        now = now.astimezone(IST)
        if now.time() < HUNT_START or now.time() >= MARKET_END:
            return CP3StockResult(stock.symbol, [], [], ["outside_hunt_window"])
        try:
            candles = aggregate_5m(stock.candles_1m)
            if not candles:
                return CP3StockResult(stock.symbol, [], [], ["no_complete_5m_candles"])
            through = len(candles) - 1
            while through >= 0 and candles[through].timestamp > now:
                through -= 1
            if through < 0:
                return CP3StockResult(stock.symbol, candles, [], ["no_completed_5m_candles"])
            bull_trend, bear_trend = self._trend_map(candles)
            bullish, bearish = self._find_latest_impulses(candles, through)
            candidates: list[PullbackCandidate] = []
            sequence = 0
            for impulse in [*bullish, *bearish]:
                sequences = self._pullback_sequences(candles, impulse, through)
                if not sequences:
                    sequence += 1
                    candidates.append(self._make_candidate(stock.symbol, impulse.direction, candles, impulse, None, STATE_IMPULSE, through, nifty_bull if impulse.direction == "LONG" else nifty_bear, bull_trend if impulse.direction == "LONG" else bear_trend, "ONE_LEG", sequence))
                    continue
                # Keep the full anatomy API, but only the latest
                # structural leg is actionable for live trading.
                for pb_type, pb in sequences[-1:]:
                    analysis = self._pullback_analysis(candles, impulse, pb)
                    if analysis is None:
                        continue
                    # A pullback is actionable only while it is still the
                    # current structure. Once its confirmed extreme is more
                    # than 30 minutes behind the current completed 5m bar,
                    # it is historical context, not a fresh setup.
                    if through - pb > 6:
                        continue
                    score, _classification = self._structure_quality(candles, impulse, analysis)
                    if score < self.MIN_STRUCTURAL_SCORE:
                        continue
                    sequence += 1
                    regime_map = nifty_bull if impulse.direction == "LONG" else nifty_bear
                    trend_map = bull_trend if impulse.direction == "LONG" else bear_trend
                    state = STATE_ARMED if candles[through].close <= PRICE_MAX and trend_map[through] is True else STATE_QUALIFIED
                    candidates.append(self._make_candidate(stock.symbol, impulse.direction, candles, impulse, analysis, state, through, regime_map, trend_map, pb_type, sequence))
            return CP3StockResult(stock.symbol, candles, candidates, [])
        except Exception as exc:
            return CP3StockResult(stock.symbol, locals().get("candles", []), [], [f"stock:{type(exc).__name__}:{exc}"])

    def cycle(self, cp2_cycle: CP2Cycle, now: datetime | None = None) -> CP3Cycle:
        ts = (now or cp2_cycle.timestamp).astimezone(IST)
        bull, bear = self._nifty_regime_maps(cp2_cycle.nifty_payload)
        results = {symbol: self.analyze_stock(stock, ts, bull, bear) for symbol, stock in cp2_cycle.stocks.items()}
        self.last_cycle = CP3Cycle(ts, results, bull, bear)
        return self.last_cycle

    def stop(self) -> None:
        self.running = False

    def health(self) -> dict[str, Any]:
        cycle = self.last_cycle
        return {"engine": "RUNNING" if self.running else "STOPPED", "stocks_analyzed": len(cycle.stock_results) if cycle else 0, "candidates": len(cycle.candidates) if cycle else 0, "armed": len(cycle.armed_candidates) if cycle else 0}
