from __future__ import annotations

import asyncio
import json
import math
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .core import MARKET_END, HUNT_START, IST, aggregate_5m, ema
from .cp2 import CP2Cycle, CP2DataEngine, StockData
from .cp4 import (
    CP4Cycle,
    CP4TriggerEngine,
    Signal,
    calculate_early_entry_from_5m,
    completed_1m_index,
    long_reacceleration,
    short_reacceleration,
)
from .core import nifty_regime


@dataclass
class MonitoringState:
    setup_id: str
    symbol: str
    direction: str
    signal_timestamp: datetime
    entry_price: float
    status: str = "TREND_VALID_AT_TRIGGER"
    invalidation_consecutive: int = 0
    last_evaluated_5m: datetime | None = None
    active: bool = True


@dataclass(frozen=True)
class FunnelCounts:
    universe: int
    price_eligible: int
    valid_data: int
    regime: int
    impulse: int
    pullback: int
    structure: int
    trend: int
    early_entry: int
    reacceleration: int
    signals: int


@dataclass(frozen=True)
class CycleReport:
    timestamp: datetime
    universe: int
    currently_calculable: int
    temporarily_skipped: int
    developing: int
    qualified: int
    armed: int
    new_signals: int
    hunter_status: str
    funnel: FunnelCounts


@dataclass
class CP5Cycle:
    timestamp: datetime
    cp2_cycle: CP2Cycle
    stock_results: dict[str, Any]
    candidates: list[Any]
    new_signals: list[Signal]
    monitoring: dict[str, MonitoringState]
    report: CycleReport | None
    errors: list[str]


class CP5ContinuousEngine:
    """CP5 continuous runtime, concurrent stock scheduling and live monitoring.

    CP1-CP4 remain the strategy implementation. CP5 owns runtime concerns:
    continuous one-minute scheduling, independent stock workers, alert-once
    delivery, monitoring, diagnostics and independent 15-minute reporting.
    """

    def __init__(
        self,
        data_engine: CP2DataEngine | None = None,
        state_path: str | Path = "pullback_state.json",
        worker_limit: int = 450,
        alert_callback: Callable[[Signal], None] | None = None,
    ) -> None:
        self.data_engine = data_engine or CP2DataEngine()
        self.state_path = Path(state_path)
        self.worker_limit = max(1, min(int(worker_limit), 450))
        self.alert_callback = alert_callback or self._default_alert
        self.running = True
        self.worker_engines: dict[str, CP4TriggerEngine] = {}
        self.monitoring: dict[str, MonitoringState] = {}
        self.alerted_setup_ids: set[str] = set()
        self.last_cycle: CP5Cycle | None = None
        self.last_report_slot: datetime | None = None
        self.runtime_errors: list[str] = []
        self._state_session_date: str | None = None
        self._session_initialized = False
        self._state_lock = threading.Lock()
        self._load_state()

    @staticmethod
    def format_engine_panel(cycle: Any) -> str:
        cp2 = getattr(cycle, "cp2_cycle", cycle)
        endpoints = getattr(cp2, "endpoints", {}) or {}
        parts = [
            f"EXPECTED={getattr(cp2, 'expected_stock_count', 450)}",
            f"STOCKS={len(getattr(cp2, 'stocks', {}) or {})}",
        ]
        for name in sorted(endpoints):
            parts.append(f"{name.upper()}={'FAIL' if getattr(endpoints[name], 'error', None) else 'OK'}")
        return "PANEL 1 — ENGINE / MARKET | " + " ".join(parts)

    @staticmethod
    def format_signal_panel(cycle: Any) -> str:
        monitoring = getattr(cycle, "monitoring", {}) or {}
        active = [s for s in monitoring.values() if getattr(s, "active", False)]
        lines = ["PANEL 3 — SIGNAL / MONITOR", f"Active triggered setups: {len(active)}"]
        for state in active:
            lines.append(f"{state.symbol} | {state.direction} | {state.status} | {state.setup_id}")
        return "\n".join(lines)

    @staticmethod
    def format_hunter_panel(cycle: Any) -> str:
        candidates = list(getattr(cycle, "candidates", []) or [])
        developing = sum(getattr(c, "state", None) == "IMPULSE_DETECTED" for c in candidates)
        qualified = sum(getattr(c, "state", None) == "QUALIFIED_PULLBACK" for c in candidates)
        armed = sum(getattr(c, "state", None) == "TRIGGER_ARMED" for c in candidates)
        return "PANEL 2 — PULLBACK HUNTER\n" f"Developing: {developing}\n" f"Qualified: {qualified}\n" f"Trigger-armed: {armed}"

    @staticmethod
    def format_cycle_panel(report: Any) -> str:
        if report is None:
            return "PANEL 4 — 15-MINUTE CYCLE | No report yet"
        f = report.funnel
        return (
            f"{report.timestamp.isoformat()} | Universe {report.universe} | "
            f"Calculable {report.currently_calculable} | Skipped {report.temporarily_skipped} | "
            f"{f.price_eligible} price | {f.valid_data} data | {f.impulse} impulse | "
            f"{f.pullback} pullback | {f.structure} structure | {f.trend} trend | "
            f"{f.early_entry} early | {f.reacceleration} reaccel | {f.signals} SIGNALS"
        )

    @staticmethod
    def _default_alert(signal: Signal) -> None:
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass

        print(
            f"ALERT | {signal.symbol} | {signal.direction} | "
            f"{signal.signal_timestamp.astimezone(IST):%Y-%m-%d %H:%M:%S IST} | "
            f"ENTRY ₹{signal.entry_price:.2f} | "
            f"{signal.trend_invalidation_status} | {signal.setup_id}",
            flush=True,
        )

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return

        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return

            session_date = raw.get("session_date")
            if isinstance(session_date, str):
                self._state_session_date = session_date

            alerted = raw.get("alerted_setup_ids", [])
            if isinstance(alerted, list):
                self.alerted_setup_ids = {str(x) for x in alerted}

            rows = raw.get("monitoring", {})
            if isinstance(rows, dict):
                for setup_id, item in rows.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        self.monitoring[str(setup_id)] = MonitoringState(
                            setup_id=str(item["setup_id"]),
                            symbol=str(item["symbol"]),
                            direction=str(item["direction"]),
                            signal_timestamp=datetime.fromisoformat(
                                item["signal_timestamp"]
                            ).astimezone(IST),
                            entry_price=float(item["entry_price"]),
                            status=str(item.get("status", "TREND_VALID_AT_TRIGGER")),
                            invalidation_consecutive=int(item.get("invalidation_consecutive", 0)),
                            last_evaluated_5m=(
                                datetime.fromisoformat(item["last_evaluated_5m"]).astimezone(IST)
                                if item.get("last_evaluated_5m")
                                else None
                            ),
                            active=bool(item.get("active", True)),
                        )
                    except Exception:
                        continue

        except Exception as exc:
            self.runtime_errors.append(f"state_load:{type(exc).__name__}:{exc}")

    def _ensure_market_session(self, session_date: str) -> None:
        """Start every IST trading day with only that day's live state.

        State files can outlive a process restart, so the persisted session
        marker alone is not trusted. Any loaded monitor from another calendar
        day invalidates the persisted monitoring/alert cache for the new run.
        """
        with self._state_lock:
            loaded_dates = {
                state.signal_timestamp.astimezone(IST).date().isoformat()
                for state in self.monitoring.values()
                if state.signal_timestamp is not None
            }
            new_day = self._state_session_date != session_date
            contaminated = bool(loaded_dates - {session_date})
            if new_day or contaminated:
                self.monitoring.clear()
                self.alerted_setup_ids.clear()
                self.last_report_slot = None
                self._state_session_date = session_date
            self._session_initialized = True

    def _dedupe_current_signals(self, signals: list[Signal]) -> list[Signal]:
        """Permit at most one active setup per symbol/direction.

        A pullback may generate a new signal after the previous one has
        completed or invalidated. It must not create a second live monitor
        every minute while the same structural episode is still active.
        """
        active_keys = {
            (state.symbol, state.direction)
            for state in self.monitoring.values()
            if state.active
        }
        accepted: list[Signal] = []
        cycle_keys: set[tuple[str, str]] = set()
        for signal in sorted(signals, key=lambda s: s.signal_timestamp):
            key = (signal.symbol, signal.direction)
            if key in active_keys or key in cycle_keys:
                continue
            accepted.append(signal)
            cycle_keys.add(key)
        return accepted

    def _save_state(self) -> None:
        payload = {
            "version": 1,
            "session_date": self._state_session_date,
            "alerted_setup_ids": sorted(self.alerted_setup_ids),
            "monitoring": {
                setup_id: {
                    "setup_id": state.setup_id,
                    "symbol": state.symbol,
                    "direction": state.direction,
                    "signal_timestamp": self._serialize_datetime(state.signal_timestamp),
                    "entry_price": state.entry_price,
                    "status": state.status,
                    "invalidation_consecutive": state.invalidation_consecutive,
                    "last_evaluated_5m": self._serialize_datetime(state.last_evaluated_5m),
                    "active": state.active,
                }
                for setup_id, state in self.monitoring.items()
            },
        }

        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp.replace(self.state_path)
        except Exception as exc:
            self.runtime_errors.append(f"state_save:{type(exc).__name__}:{exc}")
            try:
                temp.unlink(missing_ok=True)
            except Exception:
                pass

    def _worker(self, symbol: str) -> CP4TriggerEngine:
        worker = self.worker_engines.get(symbol)
        if worker is None:
            worker = CP4TriggerEngine()
            self.worker_engines[symbol] = worker
        return worker

    @staticmethod
    def _single_stock_cycle(cp2_cycle: CP2Cycle, stock: StockData) -> CP2Cycle:
        return CP2Cycle(
            timestamp=cp2_cycle.timestamp,
            endpoints={},
            stocks={stock.symbol: stock},
            nifty_payload=cp2_cycle.nifty_payload,
            nifty_error=cp2_cycle.nifty_error,
            expected_stock_count=cp2_cycle.expected_stock_count,
        )

    def _run_one_stock(self, symbol: str, stock: StockData, cp2_cycle: CP2Cycle, now: datetime):
        try:
            worker = self._worker(symbol)
            result = worker.cycle(self._single_stock_cycle(cp2_cycle, stock), now=now)
            stock_result = result.stock_results.get(symbol)
            candidates: list[Any] = []
            if worker.hunter.last_cycle is not None:
                hunter_result = worker.hunter.last_cycle.stock_results.get(symbol)
                if hunter_result is not None:
                    candidates = list(hunter_result.candidates)
            errors = list(stock_result.errors) if stock_result is not None else ["cp4_stock_result_unavailable"]
            signals = list(stock_result.signals) if stock_result is not None else []
            return symbol, stock_result, candidates, errors + [f"__SIGNALS__:{len(signals)}"]
        except Exception as exc:
            return symbol, None, [], [f"worker:{type(exc).__name__}:{exc}"]

    async def _process_universe(self, cp2_cycle: CP2Cycle, now: datetime):
        semaphore = asyncio.Semaphore(self.worker_limit)

        async def task(symbol: str, stock: StockData):
            async with semaphore:
                return await asyncio.to_thread(self._run_one_stock, symbol, stock, cp2_cycle, now)

        jobs = [task(symbol, stock) for symbol, stock in cp2_cycle.stocks.items()]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        stock_results: dict[str, Any] = {}
        candidates: list[Any] = []
        new_signals: list[Signal] = []
        errors: list[str] = []

        for item in results:
            if isinstance(item, Exception):
                errors.append(f"universe_worker:{type(item).__name__}:{item}")
                continue
            symbol, stock_result, stock_candidates, worker_errors = item
            if stock_result is not None:
                stock_results[symbol] = stock_result
            candidates.extend(stock_candidates)
            signal_count = 0
            for message in worker_errors:
                if message.startswith("__SIGNALS__:"):
                    try:
                        signal_count = int(message.split(":", 1)[1])
                    except Exception:
                        signal_count = 0
                else:
                    errors.append(f"{symbol}:{message}")
            if signal_count:
                worker = self.worker_engines.get(symbol)
                if worker is not None and worker.last_cycle is not None:
                    result = worker.last_cycle.stock_results.get(symbol)
                    if result is not None:
                        new_signals.extend(result.signals)

        deduped: dict[str, Signal] = {}
        for signal in new_signals:
            deduped.setdefault(signal.setup_id, signal)
        return stock_results, candidates, list(deduped.values()), errors

    def _candidate_funnel(self, cp2_cycle: CP2Cycle, stock_results: Mapping[str, Any], candidates: list[Any], new_signals: list[Signal], now: datetime) -> FunnelCounts:
        universe = cp2_cycle.expected_stock_count
        price_eligible = valid_data = regime = impulse = pullback = structure = trend = early_entry = reacceleration = 0
        for stock in cp2_cycle.stocks.values():
            if not stock.healthy:
                continue
            valid_data += 1
            idx = completed_1m_index(stock.candles_1m, now)
            if idx is not None and stock.candles_1m[idx].close <= 1200:
                price_eligible += 1

        nifty_bull: dict[datetime, bool] = {}
        nifty_bear: dict[datetime, bool] = {}
        if cp2_cycle.nifty_payload is not None:
            rows = cp2_cycle.nifty_payload.get("5m")
            if isinstance(rows, list):
                from .core import validate_candles
                nifty_candles, _ = validate_candles(rows)
                if nifty_candles:
                    bull, bear = nifty_regime(nifty_candles)
                    nifty_bull = {c.timestamp: bool(v) for c, v in zip(nifty_candles, bull) if v is not None}
                    nifty_bear = {c.timestamp: bool(v) for c, v in zip(nifty_candles, bear) if v is not None}

        for candidate in candidates:
            if candidate.impulse is not None:
                impulse += 1
            if candidate.pullback is None:
                continue
            pullback += 1
            if candidate.pullback.structure is not None:
                structure += 1
            if candidate.stock_trend is True:
                trend += 1
            if candidate.nifty_regime is True:
                regime += 1
            stock = cp2_cycle.stocks.get(candidate.symbol)
            worker = stock_results.get(candidate.symbol)
            candles_5m = None
            if worker is not None:
                engine = getattr(self, "worker_engines", {}).get(candidate.symbol)
                if engine is not None and engine.hunter.last_cycle is not None:
                    hr = engine.hunter.last_cycle.stock_results.get(candidate.symbol)
                    if hr is not None:
                        candles_5m = hr.candles_5m
            if stock is None or candles_5m is None:
                continue
            idx = completed_1m_index(stock.candles_1m, now)
            if idx is None:
                continue
            price = stock.candles_1m[idx].close
            ep = calculate_early_entry_from_5m(candidate, candles_5m, price)
            if ep is not None and math.isfinite(ep) and ep <= 0.45:
                early_entry += 1
            reaccel = long_reacceleration(stock.candles_1m, idx) if candidate.direction == "LONG" else short_reacceleration(stock.candles_1m, idx)
            if reaccel:
                reacceleration += 1

        return FunnelCounts(universe, price_eligible, valid_data, regime, impulse, pullback, structure, trend, early_entry, reacceleration, len(new_signals))

    @staticmethod
    def _trend_invalid_now(stock: StockData, direction: str, candle_index: int) -> bool:
        candles = aggregate_5m(stock.candles_1m)
        if candle_index < 5 or candle_index >= len(candles):
            return False
        closes = [c.close for c in candles]
        e20 = ema(closes, 20)
        e60 = ema(closes, 60)
        c = candles[candle_index].close
        if e20[candle_index] is None or e60[candle_index] is None or e20[candle_index - 5] is None:
            return False
        if direction == "LONG":
            return c < e20[candle_index] and e20[candle_index] <= e20[candle_index - 5] and e20[candle_index] <= e60[candle_index]
        if direction == "SHORT":
            return c > e20[candle_index] and e20[candle_index] >= e20[candle_index - 5] and e20[candle_index] >= e60[candle_index]
        return False

    def _monitor_signal(self, state: MonitoringState, stock: StockData) -> None:
        if not state.active:
            return
        candles = aggregate_5m(stock.candles_1m)
        if not candles:
            return
        completed = [i for i, c in enumerate(candles) if c.timestamp + timedelta(minutes=5) <= stock.last_timestamp if stock.last_timestamp]
        if not completed:
            return
        idx = completed[-1]
        ts = candles[idx].timestamp
        if state.last_evaluated_5m == ts:
            return
        invalid = self._trend_invalid_now(stock, state.direction, idx)
        state.last_evaluated_5m = ts
        if invalid:
            state.invalidation_consecutive += 1
            if state.invalidation_consecutive >= 2:
                state.status = "INVALIDATED"
                state.active = False
            else:
                state.status = "INVALIDATION_PENDING_1_OF_2"
        else:
            state.invalidation_consecutive = 0
            state.status = "TREND_VALID"

    def _update_monitoring(self, cp2_cycle: CP2Cycle, new_signals: list[Signal]) -> None:
        for signal in new_signals:
            self.monitoring.setdefault(signal.setup_id, MonitoringState(
                setup_id=signal.setup_id,
                symbol=signal.symbol,
                direction=signal.direction,
                signal_timestamp=signal.signal_timestamp,
                entry_price=signal.entry_price,
                status=signal.trend_invalidation_status,
            ))
        for state in list(self.monitoring.values()):
            stock = cp2_cycle.stocks.get(state.symbol)
            if stock is not None and stock.healthy:
                try:
                    self._monitor_signal(state, stock)
                except Exception as exc:
                    self.runtime_errors.append(f"monitor:{state.setup_id}:{type(exc).__name__}:{exc}")

    @staticmethod
    def _report_due(now: datetime, last_slot: datetime | None) -> datetime | None:
        local = now.astimezone(IST)
        if local.time() < HUNT_START or local.time() > MARKET_END:
            return None
        if local.minute % 15 != 0:
            return None
        slot = local.replace(second=0, microsecond=0)
        if last_slot == slot:
            return None
        return slot

    def _build_report(self, now: datetime, cp2_cycle: CP2Cycle, candidates: list[Any], new_signals: list[Signal], stock_results: Mapping[str, Any] | None = None) -> CycleReport:
        calculable = cp2_cycle.healthy_stock_count
        skipped = cp2_cycle.expected_stock_count - calculable
        developing = sum(c.state == "IMPULSE_DETECTED" for c in candidates)
        qualified = sum(c.state == "QUALIFIED_PULLBACK" for c in candidates)
        armed = sum(c.state == "TRIGGER_ARMED" for c in candidates)
        funnel = self._candidate_funnel(cp2_cycle, stock_results or {}, candidates, new_signals, now)
        return CycleReport(now, cp2_cycle.expected_stock_count, calculable, max(0, skipped), developing, qualified, armed, len(new_signals), "RUNNING" if self.running else "STOPPED", funnel)

    async def cycle_once(self, now: datetime | None = None) -> CP5Cycle:
        ts = (now or datetime.now(IST)).astimezone(IST)
        self._ensure_market_session(ts.date().isoformat())

        cp2_cycle = await self.data_engine.cycle(now=ts)
        stock_results, candidates, new_signals, errors = await self._process_universe(cp2_cycle, ts)
        new_signals = self._dedupe_current_signals(new_signals)

        for signal in new_signals:
            if signal.setup_id not in self.alerted_setup_ids:
                try:
                    self.alert_callback(signal)
                except Exception as exc:
                    errors.append(f"alert:{signal.setup_id}:{type(exc).__name__}:{exc}")
                finally:
                    self.alerted_setup_ids.add(signal.setup_id)

        self._update_monitoring(cp2_cycle, new_signals)
        report = None
        slot = self._report_due(ts, self.last_report_slot)
        if slot is not None:
            self.last_report_slot = slot
            try:
                report = self._build_report(ts, cp2_cycle, candidates, new_signals, stock_results)
            except Exception as exc:
                errors.append(f"report:{type(exc).__name__}:{exc}")

        cycle = CP5Cycle(ts, cp2_cycle, stock_results, candidates, new_signals, dict(self.monitoring), report, errors)
        self.last_cycle = cycle
        self._save_state()
        return cycle

    async def run_forever(self) -> None:
        self.running = True
        while self.running:
            now = datetime.now(IST)
            if now.time() > MARKET_END:
                break
            try:
                await self.cycle_once(now=now)
            except Exception as exc:
                self.runtime_errors.append(f"loop:{type(exc).__name__}:{exc}")
            current = datetime.now(IST)
            next_minute = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
            await asyncio.sleep(max(0.1, (next_minute - current).total_seconds()))

    def stop(self) -> None:
        self.running = False
        try:
            self._save_state()
        except Exception:
            pass

    def health(self) -> dict[str, Any]:
        cycle = self.last_cycle
        return {
            "engine": "RUNNING" if self.running else "STOPPED",
            "expected_stocks": cycle.cp2_cycle.expected_stock_count if cycle else 450,
            "stocks_in_last_cycle": len(cycle.cp2_cycle.stocks) if cycle else 0,
            "candidates": len(cycle.candidates) if cycle else 0,
            "new_signals": len(cycle.new_signals) if cycle else 0,
            "active_monitors": sum(s.active for s in self.monitoring.values()),
            "invalidated_monitors": sum(not s.active for s in self.monitoring.values()),
            "alerted_setup_ids": len(self.alerted_setup_ids),
            "runtime_errors": len(self.runtime_errors),
        }
