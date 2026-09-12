from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Mapping, Sequence

from .core import (
    Candle,
    IST,
    PRICE_MAX,
    HUNT_START,
    aggregate_5m,
    atr_wilder,
    canonical_structure,
    confirmed_pivots,
    ema,
    momentum_metrics,
    retracement,
    session_vwap,
    volume_ratio,
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
        return [
            c
            for r in self.stock_results.values()
            for c in r.candidates
        ]

    @property
    def armed_candidates(self) -> list[PullbackCandidate]:
        return [
            c
            for c in self.candidates
            if c.state == STATE_ARMED
        ]


class CP3PullbackHunter:
    """CP3: 5m pullback anatomy hunter; no 1m trigger firing."""

    def __init__(self) -> None:
        self.running = True
        self._sequence_by_key: dict[
            tuple[str, str, datetime, datetime], int
        ] = {}
        self.last_cycle: CP3Cycle | None = None

    @staticmethod
    def _nifty_regime_maps(
        nifty_payload: Mapping[str, Any] | None,
    ) -> tuple[dict[datetime, bool], dict[datetime, bool]]:
        if not nifty_payload:
            return {}, {}

        rows = nifty_payload.get("5m")

        if not isinstance(rows, list):
            return {}, {}

        from .core import validate_candles, nifty_regime

        candles, _ = validate_candles(rows)

        if not candles:
            return {}, {}

        bull, bear = nifty_regime(candles)

        return (
            {
                c.timestamp: bool(v)
                for c, v in zip(candles, bull)
                if v is not None
            },
            {
                c.timestamp: bool(v)
                for c, v in zip(candles, bear)
                if v is not None
            },
        )

    @staticmethod
    def _trend_map(
        candles: Sequence[Candle],
    ) -> tuple[list[bool | None], list[bool | None]]:
        closes = [c.close for c in candles]

        e20 = ema(closes, 20)
        e60 = ema(closes, 60)

        bull: list[bool | None] = [None] * len(candles)
        bear: list[bool | None] = [None] * len(candles)

        for i, c in enumerate(candles):
            if (
                i < 5
                or e20[i] is None
                or e60[i] is None
                or e20[i - 5] is None
            ):
                continue

            bull[i] = (
                c.close > e20[i]
                and e20[i] > e60[i]
                and e20[i] > e20[i - 5]
            )

            bear[i] = (
                c.close < e20[i]
                and e20[i] < e60[i]
                and e20[i] < e20[i - 5]
            )

        return bull, bear

    @staticmethod
    def _quality(
        impulse: Impulse,
        pullback: PullbackAnalysis,
        close: float,
        trend: bool | None,
        regime: bool | None,
    ) -> dict[str, float | None]:
        r = pullback.retracement

        # Quality is informational only.
        # It never vetoes an armed candidate.
        depth_score = (
            max(0.0, 1.0 - abs(r - 0.55) / 0.55)
            if math.isfinite(r)
            else None
        )

        duration_score = (
            1.0
            / (
                1.0
                + max(0, pullback.duration_bars - 3) / 10.0
            )
        )

        return {
            "impulse_atr_multiple": (
                impulse.distance / impulse.atr_at_extreme
                if impulse.atr_at_extreme > 0
                else None
            ),
            "impulse_percentage": impulse.percentage,
            "retracement_depth_score": depth_score,
            "duration_quality": duration_score,
            "momentum_ratio": pullback.momentum_ratio,
            "volume_ratio": pullback.volume_ratio,
            "vwap_side": (
                1.0
                if (
                    pullback.vwap is not None
                    and (
                        (
                            close > pullback.vwap
                        )
                        if impulse.direction == "LONG"
                        else (
                            close < pullback.vwap
                        )
                    )
                )
                else (
                    0.0
                    if pullback.vwap is not None
                    else None
                )
            ),
            "trend_confirmation": (
                1.0
                if trend is True
                else 0.0
                if trend is False
                else None
            ),
            "regime_confirmation": (
                1.0
                if regime is True
                else 0.0
                if regime is False
                else None
            ),
        }

    def _make_candidate(
        self,
        symbol: str,
        direction: str,
        candles: Sequence[Candle],
        impulse: Impulse,
        pullback: PullbackAnalysis | None,
        state: str,
        current_index: int,
        nifty_map: Mapping[datetime, bool],
        trend_map: Sequence[bool | None],
    ) -> PullbackCandidate:
        now = candles[current_index].timestamp

        regime = nifty_map.get(now)
        trend = trend_map[current_index]

        key = (
            symbol,
            direction,
            candles[impulse.impulse_low_index].timestamp,
            candles[impulse.impulse_high_index].timestamp,
        )

        seq = self._sequence_by_key.setdefault(key, 1)

        setup_id = (
            f"{symbol}-PB-"
            f"{candles[impulse.impulse_high_index].timestamp.astimezone(IST):%Y%m%d}"
            f"-{seq:03d}"
        )

        quality = (
            self._quality(
                impulse,
                pullback,
                candles[current_index].close,
                trend,
                regime,
            )
            if pullback
            else {}
        )

        return PullbackCandidate(
            setup_id=setup_id,
            symbol=symbol,
            direction=direction,
            state=state,
            created_at=candles[
                impulse.impulse_low_index
            ].timestamp,
            updated_at=now,
            impulse=impulse,
            pullback=pullback,
            current_close=candles[current_index].close,
            nifty_regime=regime,
            stock_trend=trend,
            quality=quality,
        )

    @staticmethod
    def _find_latest_impulses(
        candles: Sequence[Candle],
        through_index: int,
    ) -> tuple[list[Impulse], list[Impulse]]:
        highs, lows = confirmed_pivots(
            candles,
            through_index=through_index,
        )

        atrs = atr_wilder(candles, 14)

        bullish: list[Impulse] = []
        bearish: list[Impulse] = []

        # Every qualifying pivot pair is considered.
        # This preserves simultaneous independent setups.
        for sl in lows:
            for sh in highs:
                if sh <= sl or sh > through_index - 3:
                    continue

                atr = atrs[sh]

                if atr is None or not math.isfinite(atr):
                    continue

                d = (
                    candles[sh].high
                    - candles[sl].low
                )

                n = sh - sl

                if n >= 3 and d >= atr:
                    pct = (
                        d / candles[sl].low
                        if candles[sl].low > 0
                        else math.nan
                    )

                    bullish.append(
                        Impulse(
                            "LONG",
                            sl,
                            sh,
                            d,
                            atr,
                            n,
                            pct,
                        )
                    )

        for sh in highs:
            for sl in lows:
                if sl <= sh or sl > through_index - 3:
                    continue

                atr = atrs[sl]

                if atr is None or not math.isfinite(atr):
                    continue

                d = (
                    candles[sh].high
                    - candles[sl].low
                )

                n = sl - sh

                if n >= 3 and d >= atr:
                    pct = (
                        d / candles[sl].low
                        if candles[sl].low > 0
                        else math.nan
                    )

                    bearish.append(
                        Impulse(
                            "SHORT",
                            sh,
                            sl,
                            d,
                            atr,
                            n,
                            pct,
                        )
                    )

        return bullish, bearish

    @staticmethod
    def _next_pullback_pivot(
        candles: Sequence[Candle],
        impulse: Impulse,
        through_index: int,
    ) -> int | None:
        highs, lows = confirmed_pivots(
            candles,
            through_index=through_index,
        )

        targets = (
            lows
            if impulse.direction == "LONG"
            else highs
        )

        origin = (
            impulse.impulse_high_index
            if impulse.direction == "LONG"
            else impulse.impulse_low_index
        )

        later = [
            p
            for p in targets
            if p > origin
            and p <= through_index - 3
        ]

        return min(later) if later else None

    def _pullback_analysis(
        self,
        candles: Sequence[Candle],
        impulse: Impulse,
        pb: int,
    ) -> PullbackAnalysis | None:
        start = (
            impulse.impulse_high_index + 1
            if impulse.direction == "LONG"
            else impulse.impulse_low_index + 1
        )

        end = pb

        duration = (
            end
            - (
                impulse.impulse_high_index
                if impulse.direction == "LONG"
                else impulse.impulse_low_index
            )
        )

        if duration < 3:
            return None

        pb_extreme = (
            candles[pb].low
            if impulse.direction == "LONG"
            else candles[pb].high
        )

        r = retracement(
            impulse.direction,
            candles[
                impulse.impulse_high_index
            ].high,
            candles[
                impulse.impulse_low_index
            ].low,
            pb_extreme,
        )

        if (
            not math.isfinite(r)
            or not (0.30 <= r <= 0.80)
        ):
            return None

        structure = canonical_structure(
            candles,
            start,
            end,
            impulse.direction,
        )

        if structure is None:
            return None

        mi, mp, mr = momentum_metrics(
            candles,
            impulse.impulse_low_index,
            impulse.impulse_high_index,
            pb,
        )

        vr = volume_ratio(
            candles,
            start,
            end,
        )

        vwaps = session_vwap(candles)

        vwap = (
            vwaps[end]
            if end < len(vwaps)
            else None
        )

        denom_long = (
            candles[
                impulse.impulse_high_index
            ].high
            - candles[pb].low
        )

        denom_short = (
            candles[pb].high
            - candles[
                impulse.impulse_low_index
            ].low
        )

        ep_long = (
            (
                candles[end].close
                - candles[pb].low
            )
            / denom_long
            if denom_long > 0
            else math.nan
        )

        ep_short = (
            (
                candles[pb].high
                - candles[end].close
            )
            / denom_short
            if denom_short > 0
            else math.nan
        )

        return PullbackAnalysis(
            start,
            end,
            r,
            duration,
            structure,
            mi,
            mp,
            mr,
            vr,
            vwap,
            ep_long,
            ep_short,
        )

    def analyze_stock(
        self,
        stock: StockData,
        now: datetime,
        nifty_bull: Mapping[datetime, bool],
        nifty_bear: Mapping[datetime, bool],
    ) -> CP3StockResult:
        errors: list[str] = []

        try:
            candles = aggregate_5m(
                stock.candles_1m
            )

            if not candles:
                return CP3StockResult(
                    stock.symbol,
                    [],
                    [],
                    ["no_complete_5m_candles"],
                )

            through = len(candles) - 1

            if candles[through].timestamp > now:
                through -= 1

            if through < 0:
                return CP3StockResult(
                    stock.symbol,
                    candles,
                    [],
                    ["no_completed_5m_candles"],
                )

            bull_trend, bear_trend = (
                self._trend_map(candles)
            )

            bullish, bearish = (
                self._find_latest_impulses(
                    candles,
                    through,
                )
            )

            candidates: list[PullbackCandidate] = []

            for impulse in [
                *bullish,
                *bearish,
            ]:
                pb = self._next_pullback_pivot(
                    candles,
                    impulse,
                    through,
                )

                if pb is None:
                    trend = (
                        bull_trend[through]
                        if impulse.direction == "LONG"
                        else bear_trend[through]
                    )

                    regime = (
                        nifty_bull.get(
                            candles[through].timestamp
                        )
                        if impulse.direction == "LONG"
                        else nifty_bear.get(
                            candles[through].timestamp
                        )
                    )

                    candidates.append(
                        self._make_candidate(
                            stock.symbol,
                            impulse.direction,
                            candles,
                            impulse,
                            None,
                            STATE_IMPULSE,
                            through,
                            (
                                nifty_bull
                                if impulse.direction == "LONG"
                                else nifty_bear
                            ),
                            (
                                bull_trend
                                if impulse.direction == "LONG"
                                else bear_trend
                            ),
                        )
                    )

                    continue

                analysis = self._pullback_analysis(
                    candles,
                    impulse,
                    pb,
                )

                if analysis is None:
                    candidates.append(
                        self._make_candidate(
                            stock.symbol,
                            impulse.direction,
                            candles,
                            impulse,
                            None,
                            STATE_PULLBACK,
                            through,
                            (
                                nifty_bull
                                if impulse.direction == "LONG"
                                else nifty_bear
                            ),
                            (
                                bull_trend
                                if impulse.direction == "LONG"
                                else bear_trend
                            ),
                        )
                    )

                    continue

                direction_regime = (
                    nifty_bull
                    if impulse.direction == "LONG"
                    else nifty_bear
                )

                direction_trend = (
                    bull_trend
                    if impulse.direction == "LONG"
                    else bear_trend
                )

                regime = direction_regime.get(
                    candles[through].timestamp
                )

                trend = direction_trend[through]

                current_close = candles[
                    through
                ].close

                ep = (
                    analysis.early_entry_long
                    if impulse.direction == "LONG"
                    else analysis.early_entry_short
                )

                core_5m = (
                    current_close <= PRICE_MAX
                    and regime is True
                    and trend is True
                    and ep is not None
                    and math.isfinite(ep)
                    and ep <= 0.45
                )

                state = (
                    STATE_ARMED
                    if core_5m
                    else STATE_QUALIFIED
                )

                candidates.append(
                    self._make_candidate(
                        stock.symbol,
                        impulse.direction,
                        candles,
                        impulse,
                        analysis,
                        state,
                        through,
                        direction_regime,
                        direction_trend,
                    )
                )

            unique: dict[
                str, PullbackCandidate
            ] = {
                c.setup_id: c
                for c in candidates
            }

            return CP3StockResult(
                stock.symbol,
                candles,
                list(unique.values()),
                errors,
            )

        except Exception as exc:
            errors.append(
                f"stock:{type(exc).__name__}:{exc}"
            )

            return CP3StockResult(
                stock.symbol,
                [],
                [],
                errors,
            )

    def cycle(
        self,
        cp2_cycle: CP2Cycle,
        now: datetime | None = None,
    ) -> CP3Cycle:
        ts = (
            now or cp2_cycle.timestamp
        ).astimezone(IST)

        bull, bear = (
            self._nifty_regime_maps(
                cp2_cycle.nifty_payload
            )
        )

        results: dict[
            str, CP3StockResult
        ] = {}

        # CP3 may consume/prepare data outside
        # the hunting window, but it must not
        # create pullback candidates before
        # 10:00 or after 15:15.
        hunting_window = (
            HUNT_START <= ts.time()
            < time(15, 15)
        )

        for symbol, stock in (
            cp2_cycle.stocks.items()
        ):
            if not stock.healthy:
                continue

            if not hunting_window:
                results[symbol] = CP3StockResult(
                    stock.symbol,
                    aggregate_5m(
                        stock.candles_1m
                    ),
                    [],
                    [],
                )

                continue

            result = self.analyze_stock(
                stock,
                ts,
                bull,
                bear,
            )

            results[symbol] = result

        cycle = CP3Cycle(
            ts,
            results,
            bull,
            bear,
        )

        self.last_cycle = cycle

        return cycle

    def health(self) -> dict[str, Any]:
        c = self.last_cycle

        return {
            "engine": (
                "RUNNING"
                if self.running
                else "STOPPED"
            ),
            "stocks_analyzed": (
                len(c.stock_results)
                if c
                else 0
            ),
            "candidates": (
                len(c.candidates)
                if c
                else 0
            ),
            "qualified_pullbacks": (
                sum(
                    x.state == STATE_QUALIFIED
                    for x in c.candidates
                )
                if c
                else 0
            ),
            "trigger_armed": (
                len(c.armed_candidates)
                if c
                else 0
            ),
            "nifty_regime_points": (
                len(c.nifty_bullish)
                + len(c.nifty_bearish)
                if c
                else 0
            ),
        }