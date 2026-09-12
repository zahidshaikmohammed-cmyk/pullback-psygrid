from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from .core import Candle, HUNT_START, IST, MARKET_END, PRICE_MAX
from .cp2 import CP2Cycle, StockData
from .cp3 import (
    CP3PullbackHunter,
    PullbackCandidate,
)


STATE_TRIGGERED = "TRIGGERED"


@dataclass(frozen=True)
class Signal:
    setup_id: str
    symbol: str
    direction: str
    signal_timestamp: datetime
    entry_price: float
    trend_invalidation_status: str


@dataclass
class CP4StockResult:
    symbol: str
    signals: list[Signal]
    errors: list[str]


@dataclass
class CP4Cycle:
    timestamp: datetime
    stock_results: dict[str, CP4StockResult]

    @property
    def signals(self) -> list[Signal]:
        signals: list[Signal] = []

        for result in self.stock_results.values():
            signals.extend(result.signals)

        return signals


def long_reacceleration(
    candles: Sequence[Candle],
    index: int,
) -> bool:
    """
    Exact v1.0 LONG 1m reacceleration rule.

    C_t > max(C_t-1, C_t-2, C_t-3)
    C_t > C_t-1
    C_t - C_t-1 > (C_t - C_t-3) / 3

    Only the supplied completed 1m candle is evaluated.
    """

    if index < 3:
        return False

    c_t = candles[index].close
    c_1 = candles[index - 1].close
    c_2 = candles[index - 2].close
    c_3 = candles[index - 3].close

    return (
        c_t > max(c_1, c_2, c_3)
        and c_t > c_1
        and (c_t - c_1) > ((c_t - c_3) / 3.0)
    )


def short_reacceleration(
    candles: Sequence[Candle],
    index: int,
) -> bool:
    """
    Exact v1.0 SHORT 1m reacceleration rule.

    C_t < min(C_t-1, C_t-2, C_t-3)
    C_t < C_t-1
    C_t-1 - C_t > (C_t-3 - C_t) / 3

    Only the supplied completed 1m candle is evaluated.
    """

    if index < 3:
        return False

    c_t = candles[index].close
    c_1 = candles[index - 1].close
    c_2 = candles[index - 2].close
    c_3 = candles[index - 3].close

    return (
        c_t < min(c_1, c_2, c_3)
        and c_t < c_1
        and (c_1 - c_t) > ((c_3 - c_t) / 3.0)
    )


def completed_1m_index(
    candles: Sequence[Candle],
    now: datetime,
) -> int | None:
    """
    Return the newest genuinely completed 1m candle.

    Candle timestamps are treated as the start of their one-minute
    interval, consistent with the 1m -> 5m aggregation model.

    Therefore a candle timestamped at 10:30 is usable only once
    10:31 has arrived.
    """

    if not candles:
        return None

    now_ist = now.astimezone(IST)

    latest: int | None = None

    for i, candle in enumerate(candles):
        candle_time = candle.timestamp.astimezone(IST)

        if candle_time + timedelta(minutes=1) <= now_ist:
            latest = i

    return latest


def calculate_early_entry_from_5m(
    candidate: PullbackCandidate,
    candles_5m: Sequence[Candle],
    price: float,
) -> float | None:
    """
    Recalculate the exact v1.0 Early Entry value using the
    current completed 1m trigger close.

    LONG:
        EP = (C_t - L_PB) / (H_IMP - L_PB)

    SHORT:
        EP = (H_PB - C_t) / (H_PB - L_IMP)

    CP3 stores the pivot pair directionally:

        LONG -> impulse_low_index = swing low
                 impulse_high_index = swing high

        SHORT -> impulse_low_index = swing high
                  impulse_high_index = swing low

    Therefore CP4 interprets the indices according to direction,
    rather than assuming the field names themselves describe the
    economic role for both directions.
    """

    pullback = candidate.pullback

    if pullback is None:
        return None

    if not math.isfinite(price):
        return None

    if candidate.direction == "LONG":
        impulse_high_index = candidate.impulse.impulse_high_index
        pullback_index = pullback.pullback_end_index

        if not (
            0 <= impulse_high_index < len(candles_5m)
            and 0 <= pullback_index < len(candles_5m)
        ):
            return None

        h_imp = candles_5m[impulse_high_index].high
        l_pb = candles_5m[pullback_index].low
        denominator = h_imp - l_pb

        if denominator <= 0:
            return None

        return (price - l_pb) / denominator

    if candidate.direction == "SHORT":
        impulse_low_index = candidate.impulse.impulse_high_index
        pullback_index = pullback.pullback_end_index

        if not (
            0 <= impulse_low_index < len(candles_5m)
            and 0 <= pullback_index < len(candles_5m)
        ):
            return None

        l_imp = candles_5m[impulse_low_index].low
        h_pb = candles_5m[pullback_index].high
        denominator = h_pb - l_imp

        if denominator <= 0:
            return None

        return (h_pb - price) / denominator

    return None


def candidate_is_price_eligible(
    price: float,
) -> bool:
    """
    v1.0 hard price condition:

        Price_t <= ₹1200
    """

    return (
        math.isfinite(price)
        and price <= PRICE_MAX
    )


class CP4TriggerEngine:
    """
    CP4 — 1m Trigger Engine.

    CP3 owns 5m pullback anatomy.

    CP4:
        1. obtains a fresh CP3 calculation,
        2. evaluates only completed 1m candles,
        3. revalidates the underlying 5m setup,
        4. checks the current 1m price,
        5. checks exact 1m reacceleration,
        6. recalculates Early Entry from the current 1m close,
        7. emits exactly one signal per SetupID.

    CP4 does not implement:
        - profit targets,
        - stop-loss zones,
        - secondary hard gates,
        - artificial signal generation,
        - global stopping after a signal.
    """

    def __init__(self) -> None:
        self.running = True

        self.hunter = CP3PullbackHunter()

        # One signal maximum per SetupID.
        self.triggered_setup_ids: set[str] = set()

        self.signals: dict[str, Signal] = {}

        self.last_cycle: CP4Cycle | None = None

    @staticmethod
    def _candidate_direction_reacceleration(
        candidate: PullbackCandidate,
        candles_1m: Sequence[Candle],
        index_1m: int,
    ) -> bool:
        if candidate.direction == "LONG":
            return long_reacceleration(
                candles_1m,
                index_1m,
            )

        if candidate.direction == "SHORT":
            return short_reacceleration(
                candles_1m,
                index_1m,
            )

        return False

    @staticmethod
    def _candidate_current_trend_valid(
        candidate: PullbackCandidate,
    ) -> bool:
        """
        CP3 has freshly calculated the current completed-5m
        stock trend.

        No stale candidate snapshot is accepted.
        """

        return candidate.stock_trend is True

    @staticmethod
    def _candidate_current_regime_valid(
        candidate: PullbackCandidate,
    ) -> bool:
        """
        CP3 has freshly calculated the current completed-5m
        NIFTY directional regime.
        """

        return candidate.nifty_regime is True

    @staticmethod
    def _candidate_core_5m_valid(
        candidate: PullbackCandidate,
    ) -> bool:
        """
        A CP3 candidate with a PullbackAnalysis has already passed
        the 5m core anatomy:

            meaningful impulse
            30–80% retracement
            N_PB >= 3
            canonical A/B/C structure

        CP4 additionally requires the current trend and regime
        to remain valid.
        """

        return candidate.pullback is not None

    @staticmethod
    def _trend_invalidation_status() -> str:
        """
        CP5 owns live monitoring and the exact two-consecutive-candle
        trend invalidation state.

        CP4 must not invent an invalidation-zone width.
        """

        return "TREND_VALID_AT_TRIGGER"

    def _fresh_cp3_cycle(
        self,
        stock: StockData,
        cp2_cycle: CP2Cycle,
        now: datetime,
    ):
        """
        Recalculate the 5m setup from the latest data.

        This is deliberately performed immediately before trigger
        evaluation so CP4 never fires from an old candidate snapshot.
        """

        fresh_cycle = CP2Cycle(
            timestamp=now,
            endpoints={},
            stocks={
                stock.symbol: stock,
            },
            nifty_payload=cp2_cycle.nifty_payload,
            nifty_error=cp2_cycle.nifty_error,
            expected_stock_count=cp2_cycle.expected_stock_count,
        )

        return self.hunter.cycle(
            fresh_cycle,
            now=now,
        )

    def _evaluate_candidate(
        self,
        candidate: PullbackCandidate,
        stock: StockData,
        candles_5m: Sequence[Candle],
        index_1m: int,
        now: datetime,
    ) -> Signal | None:
        """
        Final v1.0 trigger sequence.

        5m anatomy
            ↓
        current trend
            ↓
        current regime
            ↓
        price
            ↓
        1m reacceleration
            ↓
        Early Entry
            ↓
        SIGNAL
        """

        if candidate.setup_id in self.triggered_setup_ids:
            return None

        # ---------------------------------------------------------
        # 1. Core 5m anatomy still valid?
        # ---------------------------------------------------------

        if not self._candidate_core_5m_valid(
            candidate,
        ):
            return None

        # ---------------------------------------------------------
        # 2. Current stock trend valid?
        # ---------------------------------------------------------

        if not self._candidate_current_trend_valid(
            candidate,
        ):
            return None

        # ---------------------------------------------------------
        # 3. Current NIFTY regime valid?
        # ---------------------------------------------------------

        if not self._candidate_current_regime_valid(
            candidate,
        ):
            return None

        trigger_candle = stock.candles_1m[
            index_1m
        ]

        price = trigger_candle.close

        # ---------------------------------------------------------
        # 4. Current 1m price eligible?
        # ---------------------------------------------------------

        if not candidate_is_price_eligible(
            price,
        ):
            return None

        # ---------------------------------------------------------
        # 5. Exact 1m reacceleration?
        # ---------------------------------------------------------

        if not self._candidate_direction_reacceleration(
            candidate,
            stock.candles_1m,
            index_1m,
        ):
            return None

        # ---------------------------------------------------------
        # 6. Recalculate Early Entry from current 1m price.
        # ---------------------------------------------------------

        ep = calculate_early_entry_from_5m(
            candidate,
            candles_5m,
            price,
        )

        if ep is None:
            return None

        if not math.isfinite(ep):
            return None

        if ep > 0.45:
            return None

        # ---------------------------------------------------------
        # 7. Signal.
        #
        # EntryPrice = completed 1m trigger candle close.
        # ---------------------------------------------------------

        signal = Signal(
            setup_id=candidate.setup_id,
            symbol=candidate.symbol,
            direction=candidate.direction,
            signal_timestamp=trigger_candle.timestamp,
            entry_price=price,
            trend_invalidation_status=(
                self._trend_invalidation_status()
            ),
        )

        self.triggered_setup_ids.add(
            candidate.setup_id
        )

        self.signals[
            candidate.setup_id
        ] = signal

        return signal

    def _process_stock(
        self,
        stock: StockData,
        cp2_cycle: CP2Cycle,
        now: datetime,
    ) -> CP4StockResult:
        """
        Process one stock in complete isolation.

        A failure here must never terminate CP4 globally.
        """

        errors: list[str] = []

        try:
            if not stock.healthy:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=["stock_unhealthy"],
                )

            if now.time() < HUNT_START:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[],
                )

            if now.time() >= MARKET_END:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[],
                )

            index_1m = completed_1m_index(
                stock.candles_1m,
                now,
            )

            if index_1m is None:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[
                        "no_completed_1m_candle"
                    ],
                )

            if index_1m < 3:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[
                        "insufficient_completed_1m_history"
                    ],
                )

            trigger_candle = stock.candles_1m[
                index_1m
            ]

            # Trigger cannot occur before 10:00.
            if trigger_candle.timestamp.astimezone(
                IST
            ).time() < HUNT_START:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[],
                )

            # -----------------------------------------------------
            # Fresh CP3 calculation.
            # -----------------------------------------------------

            fresh_cycle = self._fresh_cp3_cycle(
                stock,
                cp2_cycle,
                now,
            )

            fresh_result = fresh_cycle.stock_results.get(
                stock.symbol
            )

            if fresh_result is None:
                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=[
                        "fresh_cp3_stock_result_unavailable"
                    ],
                )

            if fresh_result.errors:
                errors.extend(
                    fresh_result.errors
                )

            candles_5m = (
                fresh_result.candles_5m
            )

            if not candles_5m:
                errors.append(
                    "no_complete_5m_anatomy"
                )

                return CP4StockResult(
                    symbol=stock.symbol,
                    signals=[],
                    errors=errors,
                )

            signals: list[Signal] = []

            # -----------------------------------------------------
            # Every fresh candidate is independently evaluated.
            # Multiple simultaneous signals are allowed.
            # -----------------------------------------------------

            for candidate in fresh_result.candidates:
                try:
                    signal = self._evaluate_candidate(
                        candidate,
                        stock,
                        candles_5m,
                        index_1m,
                        now,
                    )

                    if signal is not None:
                        signals.append(signal)

                except Exception as exc:
                    # One candidate/calculation failure must not
                    # suppress other candidates for the stock.
                    errors.append(
                        "candidate:"
                        f"{candidate.setup_id}:"
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    )

            return CP4StockResult(
                symbol=stock.symbol,
                signals=signals,
                errors=errors,
            )

        except Exception as exc:
            return CP4StockResult(
                symbol=stock.symbol,
                signals=[],
                errors=[
                    f"stock:{type(exc).__name__}:{exc}"
                ],
            )

    def cycle(
        self,
        cp2_cycle: CP2Cycle,
        now: datetime | None = None,
    ) -> CP4Cycle:
        """
        Process the entire supplied universe.

        Every stock is isolated.
        One signal never stops hunting.
        """

        ts = (
            now or cp2_cycle.timestamp
        ).astimezone(IST)

        results: dict[
            str,
            CP4StockResult,
        ] = {}

        for symbol, stock in cp2_cycle.stocks.items():
            try:
                results[symbol] = self._process_stock(
                    stock,
                    cp2_cycle,
                    ts,
                )

            except Exception as exc:
                # Global fault isolation boundary.
                results[symbol] = CP4StockResult(
                    symbol=symbol,
                    signals=[],
                    errors=[
                        "isolated:"
                        f"{type(exc).__name__}:"
                        f"{exc}"
                    ],
                )

        cycle = CP4Cycle(
            timestamp=ts,
            stock_results=results,
        )

        self.last_cycle = cycle

        return cycle

    def health(self) -> dict[str, Any]:
        cycle = self.last_cycle

        return {
            "engine": (
                "RUNNING"
                if self.running
                else "STOPPED"
            ),
            "stocks_processed": (
                len(cycle.stock_results)
                if cycle
                else 0
            ),
            "new_signals": (
                len(cycle.signals)
                if cycle
                else 0
            ),
            "total_triggered_setup_ids": len(
                self.triggered_setup_ids
            ),
        }