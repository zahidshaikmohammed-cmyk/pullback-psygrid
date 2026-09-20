from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .cp5 import CP5Cycle, CP5ContinuousEngine, CycleReport

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"


@dataclass
class CP6Snapshot:
    timestamp: Any
    panel_1: str
    panel_2: str
    panel_3: str
    panel_4: str


class CP6OutputSystem:
    """CP6 operator display only; it never changes strategy state."""

    def __init__(self) -> None:
        self.last_report: CycleReport | None = None
        self.last_snapshot: CP6Snapshot | None = None

    @staticmethod
    def _clear_terminal() -> None:
        try:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        except Exception:
            pass

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return "UNAVAILABLE"
        return str(value).replace("\r", " ").replace("\n", " ")

    @staticmethod
    def _box(title: str, rows: list[str], width: int = 44) -> list[str]:
        content = [CP6OutputSystem._safe_text(title)] + [CP6OutputSystem._safe_text(r) for r in rows]
        inner = max(max(20, width) - 2, *(len(x) for x in content))
        return [
            "┌" + "─" * inner + "┐",
            "│" + content[0].ljust(inner) + "│",
            "├" + "─" * inner + "┤",
            *["│" + row.ljust(inner) + "│" for row in content[1:]],
            "└" + "─" * inner + "┘",
        ]

    @staticmethod
    def _join_panels(left: list[str], right: list[str], gap: int = 3) -> str:
        height = max(len(left), len(right))
        left_width = max(len(x) for x in left)
        return "\n".join(
            (left[i] if i < len(left) else "").ljust(left_width)
            + " " * gap
            + (right[i] if i < len(right) else "")
            for i in range(height)
        )

    @staticmethod
    def panel_1(cycle: CP5Cycle) -> str:
        cp2 = cycle.cp2_cycle
        nifty = f"{RED}UNAVAILABLE{RESET}" if cp2.nifty_error else f"{GREEN}AVAILABLE{RESET}"
        endpoints = [
            f"{name.upper()}: {f'{RED}FAIL{RESET}' if endpoint.error else f'{GREEN}OK{RESET}'}"
            for name, endpoint in sorted(cp2.endpoints.items())
        ] or ["Endpoints: NO ENDPOINT RESULTS"]
        rows = [
            f"Engine: {GREEN}RUNNING{RESET}",
            f"Session: {cycle.timestamp:%Y-%m-%d %H:%M:%S} IST",
            f"NIFTY regime: {nifty}",
            f"Data: {cp2.healthy_stock_count}/{cp2.expected_stock_count} healthy",
            f"Stale: {cp2.stale_stock_count}",
            f"Active stocks: {len(cp2.stocks)}",
            f"Processed: {cycle.timestamp:%H:%M:%S} IST",
            *endpoints,
        ]
        return "\n".join(CP6OutputSystem._box("PANEL 1 — ENGINE / MARKET", rows))

    @staticmethod
    def panel_2(cycle: CP5Cycle) -> str:
        developing = [c for c in cycle.candidates if c.state == "IMPULSE_DETECTED"]
        qualified = [c for c in cycle.candidates if c.state == "QUALIFIED_PULLBACK"]
        armed = [c for c in cycle.candidates if c.state == "TRIGGER_ARMED"]
        strongest = sorted(
            [*qualified, *armed],
            key=lambda c: float(c.quality.get("impulse_atr_multiple") or 0.0),
            reverse=True,
        )[:10]
        rows = [f"Developing: {len(developing)}", f"Qualified: {len(qualified)}", f"Trigger-armed: {len(armed)}", "", "Strongest current candidates:"]
        if not strongest:
            rows.append("  None currently qualified/armed")
        for c in strongest:
            try:
                age = max(0, int((cycle.timestamp - c.created_at).total_seconds() // 60))
            except Exception:
                age = 0
            rows += [f"  {c.symbol} | {c.direction} | age={age}m", f"    {c.state}", f"    {c.setup_id}"]
        return "\n".join(CP6OutputSystem._box("PANEL 2 — PULLBACK HUNTER", rows))

    @staticmethod
    def panel_3(cycle: CP5Cycle) -> str:
        rows = [f"New signals this cycle: {len(cycle.new_signals)}"]
        for signal in cycle.new_signals:
            rows += [
                "",
                f"{signal.symbol} | {signal.direction}",
                f"  Entry: ₹{signal.entry_price:.2f}",
                f"  Signal: {signal.signal_timestamp:%H:%M:%S} IST",
                f"  SetupID: {signal.setup_id}",
                f"  Invalidation: {signal.trend_invalidation_status}",
            ]
        active = [s for s in cycle.monitoring.values() if s.active]
        inactive = [s for s in cycle.monitoring.values() if not s.active]
        rows += ["", f"Active triggered setups: {len(active)}", f"Invalidated/completed monitors: {len(inactive)}"]
        for state in active:
            rows += ["", f"{state.symbol} | {state.direction}", f"  {state.status}", f"  {state.setup_id}"]
        return "\n".join(CP6OutputSystem._box("PANEL 3 — SIGNAL / MONITOR", rows))

    @staticmethod
    def panel_4(report: CycleReport | None) -> str:
        if report is None:
            rows = ["Latest cycle report:", "  NO REPORT YET", "", "Hunter remains independent."]
            return "\n".join(CP6OutputSystem._box("PANEL 4 — 15-MINUTE CYCLE", rows))
        f = report.funnel
        rows = [
            f"{report.timestamp:%H:%M} CYCLE", "", f"Universe: {report.universe}",
            f"Currently calculable: {report.currently_calculable}", f"Temporarily skipped: {report.temporarily_skipped}", "",
            f"Developing: {report.developing}", f"Qualified: {report.qualified}", f"Armed: {report.armed}", f"New signals: {report.new_signals}", "",
            f"Hunter status: {report.hunter_status}", "", "Diagnostic funnel:",
            f"{f.universe} → {f.price_eligible} price → {f.valid_data} data → {f.impulse} impulse",
            f"{f.pullback} pullback → {f.structure} structure → {f.trend} trend",
            f"{f.early_entry} early-entry → {f.reacceleration} reacceleration → {f.signals} SIGNALS",
        ]
        return "\n".join(CP6OutputSystem._box("PANEL 4 — 15-MINUTE CYCLE", rows))

    def build_snapshot(self, cycle: CP5Cycle) -> CP6Snapshot:
        if cycle.report is not None:
            self.last_report = cycle.report
        snapshot = CP6Snapshot(
            timestamp=cycle.timestamp,
            panel_1=self.panel_1(cycle),
            panel_2=self.panel_2(cycle),
            panel_3=self.panel_3(cycle),
            panel_4=self.panel_4(self.last_report),
        )
        self.last_snapshot = snapshot
        return snapshot

    def render(self, cycle: CP5Cycle, clear: bool = True) -> str:
        snapshot = self.build_snapshot(cycle)
        if clear:
            self._clear_terminal()
        header = f"{BOLD}{CYAN}PULLBACK ENGINE v1.0 — CP6 OUTPUT SYSTEM{RESET}\n{DIM}990-stock continuous hunter | independent 15-minute reporting{RESET}\n\n"
        return header + self._join_panels(snapshot.panel_1.splitlines(), snapshot.panel_2.splitlines()) + "\n\n" + self._join_panels(snapshot.panel_3.splitlines(), snapshot.panel_4.splitlines()) + f"\n\n{DIM}CP6 = display/output only. No strategy decisions are made here.{RESET}\n"

    async def run(self, engine: CP5ContinuousEngine) -> None:
        engine.running = True
        while engine.running:
            cycle = await engine.cycle_once()
            try:
                print(self.render(cycle, clear=True), flush=True)
            except Exception as exc:
                print(f"CP6 OUTPUT ERROR (engine continues): {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            next_minute = cycle.timestamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
            await asyncio.sleep(max(0.1, (next_minute - cycle.timestamp).total_seconds()))


def render_cycle(cycle: CP5Cycle, previous_report: CycleReport | None = None) -> str:
    output = CP6OutputSystem()
    output.last_report = previous_report
    return output.render(cycle, clear=False)
