from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .core import IST, MARKET_END
from .cp5 import CP5ContinuousEngine, CP5Cycle
from .cp6 import CP6OutputSystem, CP6Snapshot
from .cp2_resilient import ResilientCP2DataEngine


@dataclass(frozen=True)
class CP7Cycle:
    timestamp: datetime
    cp5_cycle: CP5Cycle
    snapshot: CP6Snapshot
    integration_errors: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not self.integration_errors


class CP7IntegrationEngine:
    """CP7 — production integration/supervision boundary.

    CP2 supplies data, CP3 hunts anatomy, CP4 triggers, CP5 owns runtime
    state/monitoring, and CP6 renders operator output. CP7 composes those
    layers without changing strategy mathematics or signal rules.

    CP7 owns the application-level one-minute presentation loop: each
    iteration executes exactly one CP5 cycle and one CP6 snapshot. CP5 remains
    the source of truth for strategy/runtime state; CP7 is the composition and
    lifecycle boundary used by the canonical launcher.
    """

    def __init__(
        self,
        engine: CP5ContinuousEngine | None = None,
        output: CP6OutputSystem | None = None,
    ) -> None:
        if engine is None:
            state_path = os.environ.get("PULLBACK_ENGINE_STATE_PATH")
            audit_log_path = os.environ.get("PULLBACK_ENGINE_AUDIT_LOG_PATH")
            webhook_url = os.environ.get("PULLBACK_ENGINE_ALERT_WEBHOOK_URL")
            kwargs: dict[str, Any] = {}
            if state_path:
                kwargs["state_path"] = state_path
            if audit_log_path:
                kwargs["audit_log_path"] = audit_log_path
            if webhook_url:
                from .notify import build_alert_callback

                kwargs["alert_callback"] = build_alert_callback(webhook_url)
            self.engine = CP5ContinuousEngine(
                data_engine=ResilientCP2DataEngine(), **kwargs
            )
        else:
            self.engine = engine
        self.output = output or CP6OutputSystem()
        self.running = False
        self.last_cycle: CP7Cycle | None = None
        self.integration_errors: list[str] = []

    @staticmethod
    def _validate_cycle(cycle: CP5Cycle) -> list[str]:
        errors: list[str] = []
        timestamp = getattr(cycle, "timestamp", None)
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            errors.append("cycle_timestamp_not_timezone_aware")

        cp2 = getattr(cycle, "cp2_cycle", None)
        if cp2 is None:
            errors.append("cp2_cycle_missing")
            return errors

        expected = getattr(cp2, "expected_stock_count", None)
        if not isinstance(expected, int) or expected < 0:
            errors.append("invalid_expected_stock_count")

        signals = list(getattr(cycle, "new_signals", []) or [])
        ids = [getattr(signal, "setup_id", None) for signal in signals]
        if any(not setup_id for setup_id in ids):
            errors.append("signal_setup_id_missing")
        if len(ids) != len(set(ids)):
            errors.append("duplicate_signal_setup_id_in_cycle")

        monitoring = getattr(cycle, "monitoring", {}) or {}
        for signal in signals:
            state = monitoring.get(signal.setup_id)
            if state is None:
                errors.append(f"signal_without_monitor:{signal.setup_id}")
            elif state.symbol != signal.symbol or state.direction != signal.direction:
                errors.append(f"monitor_mismatch:{signal.setup_id}")

        report = getattr(cycle, "report", None)
        if report is not None:
            funnel = getattr(report, "funnel", None)
            if funnel is None:
                errors.append("report_funnel_missing")
            elif funnel.universe != report.universe:
                errors.append("report_funnel_universe_mismatch")

        return errors

    async def cycle_once(self, now: datetime | None = None) -> CP7Cycle:
        ts = (now or datetime.now(IST)).astimezone(IST)
        errors: list[str] = []
        cp5_cycle = await self.engine.cycle_once(now=ts)
        errors.extend(self._validate_cycle(cp5_cycle))

        try:
            snapshot = self.output.build_snapshot(cp5_cycle)
        except Exception as exc:
            errors.append(f"cp6_snapshot:{type(exc).__name__}:{exc}")
            self.integration_errors.extend(errors)
            raise

        result = CP7Cycle(
            timestamp=ts,
            cp5_cycle=cp5_cycle,
            snapshot=snapshot,
            integration_errors=tuple(errors),
        )
        self.last_cycle = result
        self.integration_errors.extend(errors)
        return result

    def render_last(self, clear: bool = True) -> str:
        if self.last_cycle is None:
            raise RuntimeError("no CP7 cycle has been executed")
        return self.output.render(self.last_cycle.cp5_cycle, clear=clear)

    async def run_forever(self) -> None:
        """Run the canonical one-minute CP5→CP6 application loop."""
        self.running = True
        self.engine.running = True
        try:
            while self.running and self.engine.running:
                now = datetime.now(IST)
                if now.time() > MARKET_END:
                    break
                try:
                    cycle = await self.cycle_once(now=now)
                    print(self.output.render(cycle.cp5_cycle, clear=True), flush=True)
                except Exception as exc:
                    self.integration_errors.append(
                        f"loop:{type(exc).__name__}:{exc}"
                    )
                    print(
                        f"CP7 CYCLE ERROR (engine continues): {type(exc).__name__}: {exc}",
                        flush=True,
                    )

                current = datetime.now(IST)
                next_minute = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
                await asyncio.sleep(max(0.1, (next_minute - current).total_seconds()))
        finally:
            self.running = False
            self.engine.running = False

    def stop(self) -> None:
        self.running = False
        self.engine.stop()

    def health(self) -> dict[str, Any]:
        engine_health = self.engine.health()
        return {
            "engine": "RUNNING" if self.running else "STOPPED",
            "cp5_engine": engine_health.get("engine"),
            "cp5_stocks": engine_health.get("stocks_in_last_cycle", 0),
            "candidates": engine_health.get("candidates", 0),
            "new_signals": engine_health.get("new_signals", 0),
            "active_monitors": engine_health.get("active_monitors", 0),
            "runtime_errors": engine_health.get("runtime_errors", 0),
            "integration_errors": len(self.integration_errors),
            "last_cycle_healthy": bool(self.last_cycle and self.last_cycle.healthy),
            "cp6_snapshot_available": self.last_cycle is not None,
        }
