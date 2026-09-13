from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from .core import Candle, HUNT_START, IST, MARKET_END, PRICE_MAX
from .cp2 import CP2Cycle, StockData
from .cp3 import CP3PullbackHunter, PullbackCandidate

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
        return [s for r in self.stock_results.values() for s in r.signals]


def long_reacceleration(candles: Sequence[Candle], index: int) -> bool:
    if index < 3:
        return False
    c_t, c_1, c_2, c_3 = (candles[index - n].close for n in (0, 1, 2, 3))
    return c_t > max(c_1, c_2, c_3) and c_t > c_1 and (c_t - c_1) > ((c_t - c_3) / 3.0)


def short_reacceleration(candles: Sequence[Candle], index: int) -> bool:
    if index < 3:
        return False
    c_t, c_1, c_2, c_3 = (candles[index - n].close for n in (0, 1, 2, 3))
    return c_t < min(c_1, c_2, c_3) and c_t < c_1 and (c_1 - c_t) > ((c_3 - c_t) / 3.0)


def completed_1m_index(candles: Sequence[Candle], now: datetime) -> int | None:
    if not candles:
        return None
    now_ist = now.astimezone(IST)
    latest = None
    for i, candle in enumerate(candles):
        if candle.timestamp.astimezone(IST) + timedelta(minutes=1) <= now_ist:
            latest = i
    return latest


def calculate_early_entry_from_5m(
    candidate: PullbackCandidate,
    candles_5m: Sequence[Candle],
    price: float,
) -> float | None:
    pullback = candidate.pullback
    if pullback is None or not math.isfinite(price):
        return None

    if candidate.direction == "LONG":
        hi = candidate.impulse.impulse_high_index
        pb = pullback.pullback_end_index
        if not (0 <= hi < len(candles_5m) and 0 <= pb < len(candles_5m)):
            return None
        h_imp = candles_5m[hi].high
        l_pb = candles_5m[pb].low
        denominator = h_imp - l_pb
        if denominator <= 0:
            return None
        return (price - l_pb) / denominator

    if candidate.direction == "SHORT":
        lo = candidate.impulse.impulse_high_index
        pb = pullback.pullback_end_index
        if not (0 <= lo < len(candles_5m) and 0 <= pb < len(candles_5m)):
            return None
        l_imp = candles_5m[lo].low
        h_pb = candles_5m[pb].high
        denominator = h_pb - l_imp
        if denominator <= 0:
            start = min(lo, pb)
            end = max(lo, pb)
            if start > end:
                return None
            l_imp = min(c.low for c in candles_5m[start:end + 1])
            denominator = h_pb - l_imp
        if denominator <= 0:
            return None
        return (h_pb - price) / denominator

    return None


def candidate_is_price_eligible(price: float) -> bool:
    return math.isfinite(price) and price <= PRICE_MAX


class CP4TriggerEngine:
    def __init__(self) -> None:
        self.running = True
        self.hunter = CP3PullbackHunter()
        self.triggered_setup_ids: set[str] = set()
        self.signals: dict[str, Signal] = {}
        self.last_cycle: CP4Cycle | None = None

    @staticmethod
    def _candidate_direction_reacceleration(candidate: PullbackCandidate, candles_1m: Sequence[Candle], index_1m: int) -> bool:
        if candidate.direction == "LONG":
            return long_reacceleration(candles_1m, index_1m)
        if candidate.direction == "SHORT":
            return short_reacceleration(candles_1m, index_1m)
        return False

    @staticmethod
    def _candidate_current_trend_valid(candidate: PullbackCandidate) -> bool:
        return candidate.stock_trend is True

    @staticmethod
    def _candidate_current_regime_valid(candidate: PullbackCandidate) -> bool:
        # NIFTY regime remains diagnostic on the candidate, but is no longer
        # a hard trigger veto. A valid stock setup can therefore trigger when
        # the benchmark feed is unavailable or temporarily non-confirming.
        return True

    @staticmethod
    def _candidate_core_5m_valid(candidate: PullbackCandidate) -> bool:
        return candidate.pullback is not None

    @staticmethod
    def _trend_invalidation_status() -> str:
        return "TREND_VALID_AT_TRIGGER"

    def _fresh_cp3_cycle(self, stock: StockData, cp2_cycle: CP2Cycle, now: datetime):
        fresh_cycle = CP2Cycle(
            timestamp=now,
            endpoints={},
            stocks={stock.symbol: stock},
            nifty_payload=cp2_cycle.nifty_payload,
            nifty_error=cp2_cycle.nifty_error,
            expected_stock_count=cp2_cycle.expected_stock_count,
        )
        return self.hunter.cycle(fresh_cycle, now=now)

    def _evaluate_candidate(
        self,
        candidate: PullbackCandidate,
        stock: StockData,
        candles_5m: Sequence[Candle],
        index_1m: int,
        now: datetime,
    ) -> Signal | None:
        if candidate.setup_id in self.triggered_setup_ids:
            return None
        if not self._candidate_core_5m_valid(candidate):
            return None
        if not self._candidate_current_trend_valid(candidate):
            return None
        if not self._candidate_current_regime_valid(candidate):
            return None

        trigger_candle = stock.candles_1m[index_1m]
        price = trigger_candle.close
        if not candidate_is_price_eligible(price):
            return None
        if not self._candidate_direction_reacceleration(candidate, stock.candles_1m, index_1m):
            return None

        ep = calculate_early_entry_from_5m(candidate, candles_5m, price)
        # Early-entry remains calculated and available for diagnostics, but
        # the former <= 0.45 threshold is no longer a hard signal veto.
        if ep is None or not math.isfinite(ep):
            return None

        signal = Signal(
            setup_id=candidate.setup_id,
            symbol=candidate.symbol,
            direction=candidate.direction,
            signal_timestamp=trigger_candle.timestamp,
            entry_price=price,
            trend_invalidation_status=self._trend_invalidation_status(),
        )
        self.triggered_setup_ids.add(candidate.setup_id)
        self.signals[candidate.setup_id] = signal
        return signal

    def _process_stock(self, stock: StockData, cp2_cycle: CP2Cycle, now: datetime) -> CP4StockResult:
        errors: list[str] = []
        try:
            if not stock.healthy:
                return CP4StockResult(stock.symbol, [], ["stock_unhealthy"])
            if now.time() < HUNT_START or now.time() >= MARKET_END:
                return CP4StockResult(stock.symbol, [], [])

            index_1m = completed_1m_index(stock.candles_1m, now)
            if index_1m is None:
                return CP4StockResult(stock.symbol, [], ["no_completed_1m_candle"])
            if index_1m < 3:
                return CP4StockResult(stock.symbol, [], ["insufficient_completed_1m_history"])
            if stock.candles_1m[index_1m].timestamp.astimezone(IST).time() < HUNT_START:
                return CP4StockResult(stock.symbol, [], [])

            fresh_cycle = self._fresh_cp3_cycle(stock, cp2_cycle, now)
            fresh_result = fresh_cycle.stock_results.get(stock.symbol)
            if fresh_result is None:
                return CP4StockResult(stock.symbol, [], ["fresh_cp3_stock_result_unavailable"])
            errors.extend(fresh_result.errors)
            candles_5m = fresh_result.candles_5m
            if not candles_5m:
                errors.append("no_complete_5m_anatomy")
                return CP4StockResult(stock.symbol, [], errors)

            signals: list[Signal] = []
            for candidate in fresh_result.candidates:
                try:
                    signal = self._evaluate_candidate(candidate, stock, candles_5m, index_1m, now)
                    if signal is not None:
                        signals.append(signal)
                except Exception as exc:
                    errors.append(f"candidate:{candidate.setup_id}:{type(exc).__name__}:{exc}")
            return CP4StockResult(stock.symbol, signals, errors)
        except Exception as exc:
            return CP4StockResult(stock.symbol, [], [f"stock:{type(exc).__name__}:{exc}"])

    def cycle(self, cp2_cycle: CP2Cycle, now: datetime | None = None) -> CP4Cycle:
        ts = (now or cp2_cycle.timestamp).astimezone(IST)
        results: dict[str, CP4StockResult] = {}
        for symbol, stock in cp2_cycle.stocks.items():
            try:
                results[symbol] = self._process_stock(stock, cp2_cycle, ts)
            except Exception as exc:
                results[symbol] = CP4StockResult(symbol, [], [f"isolated:{type(exc).__name__}:{exc}"])
        cycle = CP4Cycle(ts, results)
        self.last_cycle = cycle
        return cycle

    def health(self) -> dict[str, Any]:
        cycle = self.last_cycle
        return {
            "engine": "RUNNING" if self.running else "STOPPED",
            "stocks_processed": len(cycle.stock_results) if cycle else 0,
            "new_signals": len(cycle.signals) if cycle else 0,
            "total_triggered_setup_ids": len(self.triggered_setup_ids),
        }
