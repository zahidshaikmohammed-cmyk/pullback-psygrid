@'
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from .cp5 import CP5Cycle, CP5ContinuousEngine, CycleReport


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


@dataclass
class CP6Snapshot:
    timestamp: Any
    panel_1: str
    panel_2: str
    panel_3: str
    panel_4: str


class CP6OutputSystem:
    """
    CP6 output/display layer.

    CP6 does not make strategy decisions.
    CP6 does not create candidates.
    CP6 does not generate signals.
    CP6 does not alter CP5 monitoring.

    It only converts CP5 runtime state into the four required
    operator-facing panels.
    """

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
        text = str(value)
        return text.replace("\r", " ").replace("\n", " ")

    @staticmethod
    def _box(title: str, rows: list[str], width: int = 44) -> list[str]:
        """
        Render a box without silently losing diagnostic information.

        The requested width is a minimum. If a row is longer,
        the box expands to contain the complete row.
        """
        minimum_width = max(20, width)

        content = [
            CP6OutputSystem._safe_text(title),
            *(CP6OutputSystem._safe_text(row) for row in rows),
        ]

        inner = max(
            minimum_width - 2,
            *(len(value) for value in content),
        )

        output = [
            "\u250c" + "\u2500" * inner + "\u2510",
            "\u2502" + content[0].ljust(inner) + "\u2502",
            "\u251c" + "\u2500" * inner + "\u2524",
        ]

        for row in content[1:]:
            output.append(
                "\u2502" + row.ljust(inner) + "\u2502"
            )

        output.append(
            "\u2514" + "\u2500" * inner + "\u2518"
        )

        return output

    @staticmethod
    def _join_panels(
        left: list[str],
        right: list[str],
        gap: int = 3,
    ) -> str:
        height = max(len(left), len(right))
        left_width = max(len(x) for x in left)

        lines: list[str] = []

        for index in range(height):
            left_line = left[index] if index < len(left) else ""
            right_line = right[index] if index < len(right) else ""

            lines.append(
                left_line.ljust(left_width)
                + (" " * gap)
                + right_line
            )

        return "\n".join(lines)

    @staticmethod
    def panel_1(cycle: CP5Cycle) -> str:
        cp2 = cycle.cp2_cycle

        nifty_status = (
            f"{RED}UNAVAILABLE{RESET}"
            if cp2.nifty_error
            else f"{GREEN}AVAILABLE{RESET}"
        )

        endpoint_rows: list[str] = []

        for name, endpoint in sorted(cp2.endpoints.items()):
            endpoint_status = (
                f"{GREEN}OK{RESET}"
                if not endpoint.error
                else f"{RED}FAIL{RESET}"
            )

            endpoint_rows.append(
                f"{name.upper()}: {endpoint_status}"
            )

        if not endpoint_rows:
            endpoint_rows.append(
                "Endpoints: NO ENDPOINT RESULTS"
            )

        rows = [
            f"Engine: {GREEN}RUNNING{RESET}",
            f"Session: {cycle.timestamp:%Y-%m-%d %H:%M:%S} IST",
            f"NIFTY regime: {nifty_status}",
            (
                f"Data: {cp2.healthy_stock_count}/"
                f"{cp2.expected_stock_count} healthy"
            ),
            f"Stale: {cp2.stale_stock_count}",
            f"Active stocks: {len(cp2.stocks)}",
            f"Processed: {cycle.timestamp:%H:%M:%S} IST",
            *endpoint_rows,
        ]

        return "\n".join(
            CP6OutputSystem._box(
                "PANEL 1 \u2014 ENGINE / MARKET",
                rows,
            )
        )

    @staticmethod
    def panel_2(cycle: CP5Cycle) -> str:
        developing = [
            c
            for c in cycle.candidates
            if c.state == "IMPULSE_DETECTED"
        ]

        qualified = [
            c
            for c in cycle.candidates
            if c.state == "QUALIFIED_PULLBACK"
        ]

        armed = [
            c
            for c in cycle.candidates
            if c.state == "TRIGGER_ARMED"
        ]

        strongest = sorted(
            [*qualified, *armed],
            key=lambda candidate: float(
                candidate.quality.get(
                    "impulse_atr_multiple"
                ) or 0.0
            ),
            reverse=True,
        )[:10]

        rows = [
            f"Developing: {len(developing)}",
            f"Qualified: {len(qualified)}",
            f"Trigger-armed: {len(armed)}",
            "",
            "Strongest current candidates:",
        ]

        if not strongest:
            rows.append(
                "  None currently qualified/armed"
            )

        for candidate in strongest:
            try:
                age_minutes = max(
                    0,
                    int(
                        (
                            cycle.timestamp
                            - candidate.created_at
                        ).total_seconds()
                        // 60
                    ),
                )
            except Exception:
                age_minutes = 0

            rows.append(
                f"  {candidate.symbol} | "
                f"{candidate.direction} | "
                f"age={age_minutes}m"
            )
            rows.append(
                f"    {candidate.state}"
            )
            rows.append(
                f"    {candidate.setup_id}"
            )

        return "\n".join(
            CP6OutputSystem._box(
                "PANEL 2 \u2014 PULLBACK HUNTER",
                rows,
            )
        )

    @staticmethod
    def panel_3(cycle: CP5Cycle) -> str:
        rows = [
            f"New signals this cycle: "
            f"{len(cycle.new_signals)}"
        ]

        if cycle.new_signals:
            rows.append("")

            for signal in cycle.new_signals:
                rows.append(
                    f"{signal.symbol} | "
                    f"{signal.direction}"
                )
                rows.append(
                    f"  Entry: \u20b9"
                    f"{signal.entry_price:.2f}"
                )
                rows.append(
                    f"  Signal: "
                    f"{signal.signal_timestamp:%H:%M:%S} IST"
                )
                rows.append(
                    f"  SetupID: {signal.setup_id}"
                )
                rows.append(
                    f"  Invalidation: "
                    f"{signal.trend_invalidation_status}"
                )

        active = [
            state
            for state in cycle.monitoring.values()
            if state.active
        ]

        invalidated = [
            state
            for state in cycle.monitoring.values()
            if not state.active
        ]

        rows.extend(
            [
                "",
                f"Active triggered setups: {len(active)}",
                (
                    "Invalidated/completed monitors: "
                    f"{len(invalidated)}"
                ),
            ]
        )

        if active:
            rows.append("")

            for state in active:
                rows.append(
                    f"{state.symbol} | "
                    f"{state.direction}"
                )
                rows.append(
                    f"  {state.status}"
                )
                rows.append(
                    f"  {state.setup_id}"
                )

        return "\n".join(
            CP6OutputSystem._box(
                "PANEL 3 \u2014 SIGNAL / MONITOR",
                rows,
            )
        )

    @staticmethod
    def panel_4(
        report: CycleReport | None,
    ) -> str:
        if report is None:
            rows = [
                "Latest cycle report:",
                "  NO REPORT YET",
                "",
                "Hunter remains independent.",
            ]

            return "\n".join(
                CP6OutputSystem._box(
                    "PANEL 4 \u2014 15-MINUTE CYCLE",
                    rows,
                )
            )

        funnel = report.funnel

        rows = [
            f"{report.timestamp:%H:%M} CYCLE",
            "",
            f"Universe: {report.universe}",
            (
                "Currently calculable: "
                f"{report.currently_calculable}"
            ),
            (
                "Temporarily skipped: "
                f"{report.temporarily_skipped}"
            ),
            "",
            f"Developing: {report.developing}",
            f"Qualified: {report.qualified}",
            f"Armed: {report.armed}",
            f"New signals: {report.new_signals}",
            "",
            f"Hunter status: {report.hunter_status}",
            "",
            "Diagnostic funnel:",
            (
                f"{funnel.universe} \u2192 "
                f"{funnel.price_eligible} price \u2192 "
                f"{funnel.valid_data} data \u2192 "
                f"{funnel.impulse} impulse"
            ),
            (
                f"{funnel.pullback} pullback \u2192 "
                f"{funnel.structure} structure \u2192 "
                f"{funnel.trend} trend"
            ),
            (
                f"{funnel.early_entry} early-entry \u2192 "
                f"{funnel.reacceleration} reacceleration \u2192 "
                f"{funnel.signals} SIGNALS"
            ),
        ]

        return "\n".join(
            CP6OutputSystem._box(
                "PANEL 4 \u2014 15-MINUTE CYCLE",
                rows,
            )
        )

    def build_snapshot(
        self,
        cycle: CP5Cycle,
    ) -> CP6Snapshot:
        if cycle.report is not None:
            self.last_report = cycle.report

        snapshot = CP6Snapshot(
            timestamp=cycle.timestamp,
            panel_1=self.panel_1(cycle),
            panel_2=self.panel_2(cycle),
            panel_3=self.panel_3(cycle),
            panel_4=self.panel_4(
                self.last_report
            ),
        )

        self.last_snapshot = snapshot
        return snapshot

    def render(
        self,
        cycle: CP5Cycle,
        clear: bool = True,
    ) -> str:
        snapshot = self.build_snapshot(cycle)

        if clear:
            self._clear_terminal()

        header = (
            f"{BOLD}{CYAN}"
            "PULLBACK ENGINE v1.0 "
            "\u2014 CP6 OUTPUT SYSTEM"
            f"{RESET}\n"
            f"{DIM}"
            "450-stock continuous hunter | "
            "independent 15-minute reporting"
            f"{RESET}\n\n"
        )

        top = self._join_panels(
            snapshot.panel_1.splitlines(),
            snapshot.panel_2.splitlines(),
        )

        bottom = self._join_panels(
            snapshot.panel_3.splitlines(),
            snapshot.panel_4.splitlines(),
        )

        footer = (
            "\n\n"
            f"{DIM}"
            "CP6 = display/output only. "
            "No strategy decisions are made here."
            f"{RESET}\n"
        )

        return (
            header
            + top
            + "\n\n"
            + bottom
            + footer
        )

    async def run(
        self,
        engine: CP5ContinuousEngine,
    ) -> None:
        engine.running = True

        while engine.running:
            cycle = await engine.cycle_once()

            try:
                output = self.render(
                    cycle,
                    clear=True,
                )
                print(
                    output,
                    flush=True,
                )
            except Exception as exc:
                print(
                    "CP6 OUTPUT ERROR "
                    "(engine continues): "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )

            now = cycle.timestamp

            from datetime import timedelta

            next_minute = (
                now.replace(
                    second=0,
                    microsecond=0,
                )
                + timedelta(minutes=1)
            )

            delay = (
                next_minute - now
            ).total_seconds()

            await asyncio.sleep(
                max(0.1, delay)
            )


def render_cycle(
    cycle: CP5Cycle,
    previous_report: CycleReport | None = None,
) -> str:
    output = CP6OutputSystem()

    if previous_report is not None:
        output.last_report = previous_report

    return output.render(
        cycle,
        clear=False,
    )
'@ | Set-Content "pullback_engine\cp6.py" -Encoding utf8
Write-Host "CP6 restored cleanly."