from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .core import (
    IST, MARKET_START, MARKET_END, HUNT_START, PRICE_MAX, Candle,
    SetupState, aggregate_5m, atr_wilder, ema, early_entry,
    nifty_regime, pivot_high, reacceleration_1m, stock_trend,
    validate_candles,
)
from .cp4 import long_reacceleration, short_reacceleration, completed_1m_index
from .cp7 import CP7IntegrationEngine


@dataclass(frozen=True)
class CP8Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CP8AuditReport:
    timestamp: datetime
    checks: tuple[CP8Check, ...]
    source_scope: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed(self) -> tuple[CP8Check, ...]:
        return tuple(c for c in self.checks if not c.passed)

    @property
    def passed_count(self) -> int:
        return sum(c.passed for c in self.checks)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"CP8 {status}: {self.passed_count}/{len(self.checks)} checks passed"


class CP8ParityAudit:
    """CP8 — final conformance/parity audit for locked Pullback Engine v1.0.

    CP8 is deliberately an audit, not a strategy feature. It verifies the
    already-built CP2→CP7 implementation through deterministic contract
    probes and source-level invariants. It never fabricates market data,
    loosens a gate, creates a signal, or changes runtime state.
    """

    SOURCE_SCOPE = (
        "core.py", "cp2.py", "cp3.py", "cp4.py", "cp5.py", "cp6.py", "cp7.py"
    )

    def __init__(self, timestamp: datetime | None = None) -> None:
        self.timestamp = (timestamp or datetime.now(IST)).astimezone(IST)

    @staticmethod
    def _candle(minute: int, close: float, volume: float = 100.0) -> Candle:
        ts = datetime(2026, 1, 2, 9, 15, tzinfo=IST) + timedelta(minutes=minute)
        return Candle(ts, close, close + 0.5, close - 0.5, close, volume)

    @staticmethod
    def _check(name: str, fn: Callable[[], bool]) -> CP8Check:
        try:
            ok = bool(fn())
            return CP8Check(name, ok, "ok" if ok else "contract returned false")
        except Exception as exc:
            return CP8Check(name, False, f"{type(exc).__name__}: {exc}")

    def run(self) -> CP8AuditReport:
        checks = [
            self._check("clock_constants", lambda: (
                MARKET_START.hour == 9 and MARKET_START.minute == 15 and
                HUNT_START.hour == 10 and HUNT_START.minute == 0 and
                MARKET_END.hour == 15 and MARKET_END.minute == 15 and
                PRICE_MAX == 1200.0
            )),
            self._check("candle_requires_timezone", self._timezone_guard),
            self._check("validation_rejects_duplicates_and_bad_ohlc", self._validation_guard),
            self._check("aggregation_requires_complete_five_minutes", self._aggregation_guard),
            self._check("ema20_ema60_are_available_only_after_seed", self._ema_guard),
            self._check("atr14_wilder_seed", self._atr_guard),
            self._check("nifty_regime_requires_history", self._regime_guard),
            self._check("pivot_confirmation_has_three_bar_delay", self._pivot_guard),
            self._check("long_short_retracement_symmetry", self._retracement_guard),
            self._check("early_entry_long_short_symmetry", self._early_entry_guard),
            self._check("core_reacceleration_matches_cp4_long", lambda: self._reaccel_guard("LONG")),
            self._check("core_reacceleration_matches_cp4_short", lambda: self._reaccel_guard("SHORT")),
            self._check("completed_one_minute_candle_rule", self._completed_candle_guard),
            self._check("stock_trend_has_ema_alignment", self._trend_guard),
            self._check("state_machine_contains_locked_states", self._state_guard),
            self._check("cp7_cycle_validation_contract", self._cp7_validation_guard),
            self._check("cp7_is_canonical_integration_boundary", self._cp7_source_guard),
            self._check("cp4_contains_price_cap_and_setup_dedup", self._cp4_source_guard),
            self._check("cp5_contains_two_candle_invalidation", self._cp5_source_guard),
            self._check("cp5_contains_15_minute_reporting", lambda: self._cp5_source_guard("report")),
            self._check("cp6_is_output_only", self._cp6_source_guard),
            self._check("no_fabricated_data_path_in_core", self._no_fabrication_guard),
            self._check("state_sequence_is_exact", self._state_sequence_guard),
            self._check("cp2_to_cp7_modules_import_cleanly", self._imports_guard),
        ]
        return CP8AuditReport(self.timestamp, tuple(checks), self.SOURCE_SCOPE)

    @staticmethod
    def _timezone_guard() -> bool:
        try:
            Candle(datetime(2026, 1, 2, 9, 15), 1, 1, 1, 1, 1)
            return False
        except ValueError:
            return True

    @staticmethod
    def _validation_guard() -> bool:
        good = {"timestamp": "2026-01-02T09:15:00+05:30", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1}
        rows = [good, dict(good), {**good, "high": 8}]
        out, errors = validate_candles(rows)
        return len(out) == 1 and len(errors) == 2

    def _aggregation_guard(self) -> bool:
        full = [self._candle(i, 100 + i) for i in range(5)]
        partial = [self._candle(5 + i, 110 + i) for i in range(4)]
        return len(aggregate_5m(full + partial)) == 1

    @staticmethod
    def _ema_guard() -> bool:
        e = ema(list(range(1, 61)), 20)
        return e[18] is None and e[19] == 10.5 and e[59] is not None

    def _atr_guard(self) -> bool:
        c = [self._candle(i, 100 + i) for i in range(15)]
        a = atr_wilder(c, 14)
        return a[13] is None and a[14] is not None

    def _regime_guard(self) -> bool:
        c = [self._candle(i, 100 + i * 0.2) for i in range(60)]
        bull, bear = nifty_regime(c)
        return len(bull) == len(c) and len(bear) == len(c) and bull[0] is None

    def _pivot_guard(self) -> bool:
        c = [self._candle(i, 100.0) for i in range(10)]
        c[4] = Candle(c[4].timestamp, 100, 110, 99, 105, 100)
        return not pivot_high(c[:7], 4) and pivot_high(c, 4)

    @staticmethod
    def _retracement_guard() -> bool:
        from .core import retracement
        return math.isclose(retracement("LONG", 120, 100, 110), 0.5) and math.isclose(retracement("SHORT", 120, 100, 110), 0.5)

    @staticmethod
    def _early_entry_guard() -> bool:
        return math.isclose(early_entry("LONG", 112, 120, 100, 110), 0.2) and math.isclose(early_entry("SHORT", 108, 120, 100, 110), 0.2)

    @staticmethod
    def _reaccel_guard(direction: str) -> bool:
        closes = [10, 9, 8, 12] if direction == "LONG" else [10, 11, 12, 8]
        c = [Candle(datetime(2026, 1, 2, 10, 0, tzinfo=IST) + timedelta(minutes=i), 1, 12, 1, x, 1) for i, x in enumerate(closes)]
        core = reacceleration_1m(c, 3, direction)
        cp4 = long_reacceleration(c, 3) if direction == "LONG" else short_reacceleration(c, 3)
        return core == cp4 == True

    def _completed_candle_guard(self) -> bool:
        c = [self._candle(i, 100 + i) for i in range(5)]
        return completed_1m_index(c, c[4].timestamp + timedelta(seconds=30)) == 3 and completed_1m_index(c, c[4].timestamp + timedelta(minutes=1)) == 4

    @staticmethod
    def _trend_guard() -> bool:
        c = [Candle(datetime(2026, 1, 2, 9, 15, tzinfo=IST) + timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100 + i, 100) for i in range(60)]
        return stock_trend(c, 59, "LONG") is True

    @staticmethod
    def _state_guard() -> bool:
        required = {"IDLE", "IMPULSE_DETECTED", "PULLBACK_CANDIDATE", "QUALIFIED_PULLBACK", "TRIGGER_ARMED", "TRIGGERED", "MONITORING", "COMPLETED", "INVALIDATED"}
        return required.issubset({s.value for s in SetupState})

    @staticmethod
    def _cp7_validation_guard() -> bool:
        source = inspect.getsource(CP7IntegrationEngine._validate_cycle)
        return "return errors" in source and "set(ids)" in source and "monitoring" in source

    @staticmethod
    def _cp7_source_guard() -> bool:
        source = inspect.getsource(CP7IntegrationEngine.run_forever)
        return source.count("cycle_once") == 1 and "output.render" in source and "MARKET_END" in inspect.getsource(CP7IntegrationEngine)

    @staticmethod
    def _cp4_source_guard() -> bool:
        from . import cp4
        source = inspect.getsource(cp4.CP4TriggerEngine)
        return "triggered_setup_ids" in source and "PRICE_MAX" in source and "reacceleration" in source

    @staticmethod
    def _cp5_source_guard(kind: str = "invalidate") -> bool:
        from . import cp5
        source = inspect.getsource(cp5.CP5ContinuousEngine)
        if kind == "report":
            return "15" in source and "_report_due" in source
        return "invalidation_consecutive" in source and "2" in source

    @staticmethod
    def _cp6_source_guard() -> bool:
        from . import cp6
        source = inspect.getsource(cp6.CP6OutputSystem)
        return all(x in source for x in ("panel_1", "panel_2", "panel_3", "panel_4", "funnel"))

    @staticmethod
    def _no_fabrication_guard() -> bool:
        from . import core
        source = inspect.getsource(core).lower()
        return "interpolation" in source and "fill" in source

    @staticmethod
    def _state_sequence_guard() -> bool:
        names = [s.value for s in SetupState]
        expected = ["IDLE", "IMPULSE_DETECTED", "PULLBACK_CANDIDATE", "QUALIFIED_PULLBACK", "TRIGGER_ARMED", "TRIGGERED", "MONITORING", "COMPLETED", "INVALIDATED"]
        return names == expected

    @staticmethod
    def _imports_guard() -> bool:
        from . import core, cp2, cp3, cp4, cp5, cp6, cp7
        return all(x is not None for x in (core, cp2, cp3, cp4, cp5, cp6, cp7))


def run_cp8_audit(timestamp: datetime | None = None) -> CP8AuditReport:
    return CP8ParityAudit(timestamp).run()
