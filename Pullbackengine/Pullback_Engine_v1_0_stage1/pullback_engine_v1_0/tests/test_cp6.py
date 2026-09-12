from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pullback_engine.core import IST
from pullback_engine.cp4 import Signal
from pullback_engine.cp5 import (
    CP5Cycle,
    CycleReport,
    FunnelCounts,
    MonitoringState,
)
from pullback_engine.cp6 import (
    CP6OutputSystem,
    CP6Snapshot,
    render_cycle,
)


def make_funnel() -> FunnelCounts:
    return FunnelCounts(
        universe=450,
        price_eligible=300,
        valid_data=440,
        regime=25,
        impulse=18,
        pullback=12,
        structure=9,
        trend=8,
        early_entry=6,
        reacceleration=3,
        signals=2,
    )


def make_report() -> CycleReport:
    return CycleReport(
        timestamp=datetime(
            2026,
            9,
            12,
            10,
            30,
            tzinfo=IST,
        ),
        universe=450,
        currently_calculable=447,
        temporarily_skipped=3,
        developing=14,
        qualified=8,
        armed=3,
        new_signals=2,
        hunter_status="RUNNING",
        funnel=make_funnel(),
    )


def make_cycle(
    report: CycleReport | None = None,
    signals: list[Signal] | None = None,
    monitoring: dict[str, MonitoringState] | None = None,
):
    return type(
        "FakeCycle",
        (),
        {
            "timestamp": datetime(
                2026,
                9,
                12,
                10,
                30,
                tzinfo=IST,
            ),
            "cp2_cycle": type(
                "FakeCP2",
                (),
                {
                    "healthy_stock_count": 447,
                    "stale_stock_count": 3,
                    "expected_stock_count": 450,
                    "nifty_error": None,
                    "stocks": {
                        "TEST": object(),
                    },
                    "endpoints": {
                        "A": type(
                            "Endpoint",
                            (),
                            {"error": None},
                        )(),
                        "B": type(
                            "Endpoint",
                            (),
                            {"error": None},
                        )(),
                    },
                },
            )(),
            "candidates": [],
            "new_signals": signals or [],
            "monitoring": monitoring or {},
            "report": report,
        },
    )()


def test_output_system_initializes():
    output = CP6OutputSystem()

    assert output.last_report is None
    assert output.last_snapshot is None


def test_panel_1_contains_required_title():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "PANEL 1 — ENGINE / MARKET" in text


def test_panel_1_contains_engine_state():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "Engine:" in text
    assert "RUNNING" in text


def test_panel_1_contains_session_time():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "2026-09-12 10:30:00 IST" in text


def test_panel_1_contains_nifty_status():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "NIFTY regime:" in text
    assert "AVAILABLE" in text


def test_panel_1_contains_data_status():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "447/450 healthy" in text
    assert "Stale: 3" in text


def test_panel_1_contains_active_stock_count():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "Active stocks: 1" in text


def test_panel_1_contains_endpoint_status():
    text = CP6OutputSystem.panel_1(
        make_cycle()
    )

    assert "A:" in text
    assert "OK" in text
    assert "B:" in text


def test_panel_1_reports_nifty_unavailable():
    cycle = make_cycle()

    cycle.cp2_cycle.nifty_error = "nifty_failed"

    text = CP6OutputSystem.panel_1(
        cycle
    )

    assert "UNAVAILABLE" in text


def test_panel_1_reports_endpoint_failure():
    cycle = make_cycle()

    cycle.cp2_cycle.endpoints["A"].error = (
        "connection_failed"
    )

    text = CP6OutputSystem.panel_1(
        cycle
    )

    assert "A:" in text
    assert "FAIL" in text


def test_panel_2_contains_required_title():
    text = CP6OutputSystem.panel_2(
        make_cycle()
    )

    assert "PANEL 2 — PULLBACK HUNTER" in text


def test_panel_2_empty_candidate_counts():
    text = CP6OutputSystem.panel_2(
        make_cycle()
    )

    assert "Developing: 0" in text
    assert "Qualified: 0" in text
    assert "Trigger-armed: 0" in text


def test_panel_2_empty_candidates_message():
    text = CP6OutputSystem.panel_2(
        make_cycle()
    )

    assert "None currently qualified/armed" in text


def test_panel_2_contains_strong_candidate():
    candidate = type(
        "Candidate",
        (),
        {
            "state": "QUALIFIED_PULLBACK",
            "symbol": "RELIANCE",
            "direction": "LONG",
            "created_at": datetime(
                2026,
                9,
                12,
                10,
                0,
                tzinfo=IST,
            ),
            "setup_id": "RELIANCE-PB-001",
            "quality": {
                "impulse_atr_multiple": 2.0,
            },
        },
    )()

    cycle = make_cycle()
    cycle.candidates = [candidate]

    text = CP6OutputSystem.panel_2(
        cycle
    )

    assert "Developing: 0" in text
    assert "Qualified: 1" in text
    assert "RELIANCE" in text
    assert "LONG" in text
    assert "RELIANCE-PB-001" in text


def test_panel_2_contains_trigger_armed_candidate():
    candidate = type(
        "Candidate",
        (),
        {
            "state": "TRIGGER_ARMED",
            "symbol": "TCS",
            "direction": "SHORT",
            "created_at": datetime(
                2026,
                9,
                12,
                10,
                15,
                tzinfo=IST,
            ),
            "setup_id": "TCS-PB-001",
            "quality": {
                "impulse_atr_multiple": 3.0,
            },
        },
    )()

    cycle = make_cycle()
    cycle.candidates = [candidate]

    text = CP6OutputSystem.panel_2(
        cycle
    )

    assert "Trigger-armed: 1" in text
    assert "TCS" in text
    assert "SHORT" in text


def test_panel_2_limits_strongest_candidates_to_ten():
    candidates = []

    for i in range(15):
        candidates.append(
            type(
                "Candidate",
                (),
                {
                    "state": "QUALIFIED_PULLBACK",
                    "symbol": f"STOCK{i}",
                    "direction": "LONG",
                    "created_at": datetime(
                        2026,
                        9,
                        12,
                        10,
                        0,
                        tzinfo=IST,
                    ),
                    "setup_id": f"SETUP-{i}",
                    "quality": {
                        "impulse_atr_multiple": float(i),
                    },
                },
            )()
        )

    cycle = make_cycle()
    cycle.candidates = candidates

    text = CP6OutputSystem.panel_2(
        cycle
    )

    assert "Qualified: 15" in text
    assert "STOCK14" in text
    assert "STOCK4" not in text


def test_panel_3_contains_required_title():
    text = CP6OutputSystem.panel_3(
        make_cycle()
    )

    assert "PANEL 3 — SIGNAL / MONITOR" in text


def test_panel_3_empty_signal_count():
    text = CP6OutputSystem.panel_3(
        make_cycle()
    )

    assert "New signals this cycle: 0" in text


def test_panel_3_empty_monitor_count():
    text = CP6OutputSystem.panel_3(
        make_cycle()
    )

    assert "Active triggered setups: 0" in text


def test_panel_3_displays_signal_details():
    signal = Signal(
        setup_id="INFY-PB-001",
        symbol="INFY",
        direction="LONG",
        signal_timestamp=datetime(
            2026,
            9,
            12,
            10,
            31,
            tzinfo=IST,
        ),
        entry_price=1500.25,
        trend_invalidation_status=(
            "TREND_VALID_AT_TRIGGER"
        ),
    )

    text = CP6OutputSystem.panel_3(
        make_cycle(
            signals=[signal]
        )
    )

    assert "New signals this cycle: 1" in text
    assert "INFY" in text
    assert "LONG" in text
    assert "₹1500.25" in text
    assert "10:31:00 IST" in text
    assert "INFY-PB-001" in text
    assert "TREND_VALID_AT_TRIGGER" in text


def test_panel_3_displays_short_signal():
    signal = Signal(
        setup_id="TCS-PB-002",
        symbol="TCS",
        direction="SHORT",
        signal_timestamp=datetime(
            2026,
            9,
            12,
            11,
            0,
            tzinfo=IST,
        ),
        entry_price=3200.0,
        trend_invalidation_status=(
            "TREND_VALID_AT_TRIGGER"
        ),
    )

    text = CP6OutputSystem.panel_3(
        make_cycle(
            signals=[signal]
        )
    )

    assert "TCS" in text
    assert "SHORT" in text
    assert "₹3200.00" in text


def test_panel_3_displays_active_monitor():
    state = MonitoringState(
        setup_id="RELIANCE-PB-003",
        symbol="RELIANCE",
        direction="LONG",
        signal_timestamp=datetime(
            2026,
            9,
            12,
            11,
            0,
            tzinfo=IST,
        ),
        entry_price=2500.0,
        status="TREND_VALID",
        active=True,
    )

    text = CP6OutputSystem.panel_3(
        make_cycle(
            monitoring={
                state.setup_id: state
            }
        )
    )

    assert "Active triggered setups: 1" in text
    assert "RELIANCE" in text
    assert "LONG" in text
    assert "TREND_VALID" in text
    assert "RELIANCE-PB-003" in text


def test_panel_3_counts_inactive_monitors():
    state = MonitoringState(
        setup_id="TEST-PB-004",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=datetime(
            2026,
            9,
            12,
            11,
            0,
            tzinfo=IST,
        ),
        entry_price=100.0,
        status="INVALIDATED",
        active=False,
    )

    text = CP6OutputSystem.panel_3(
        make_cycle(
            monitoring={
                state.setup_id: state
            }
        )
    )

    assert "Active triggered setups: 0" in text
    assert "Invalidated/completed monitors: 1" in text


def test_panel_4_without_report():
    output = CP6OutputSystem()

    text = output.panel_4(None)

    assert "PANEL 4 — 15-MINUTE CYCLE" in text
    assert "NO REPORT YET" in text


def test_panel_4_contains_cycle_summary():
    text = CP6OutputSystem.panel_4(
        make_report()
    )

    assert "10:30 CYCLE" in text
    assert "Universe: 450" in text
    assert "Currently calculable: 447" in text
    assert "Temporarily skipped: 3" in text
    assert "Developing: 14" in text
    assert "Qualified: 8" in text
    assert "Armed: 3" in text
    assert "New signals: 2" in text
    assert "Hunter status: RUNNING" in text


def test_panel_4_contains_funnel():
    text = CP6OutputSystem.panel_4(
        make_report()
    )

    assert "Diagnostic funnel:" in text
    assert "450" in text
    assert "300 price" in text
    assert "440 data" in text
    assert "18 impulse" in text
    assert "12 pullback" in text
    assert "9 structure" in text
    assert "8 trend" in text
    assert "6 early-entry" in text
    assert "3 reacceleration" in text
    assert "2 SIGNALS" in text


def test_build_snapshot_returns_snapshot():
    output = CP6OutputSystem()

    cycle = make_cycle(
        report=make_report()
    )

    snapshot = output.build_snapshot(
        cycle
    )

    assert isinstance(
        snapshot,
        CP6Snapshot,
    )

    assert snapshot.timestamp == (
        cycle.timestamp
    )


def test_build_snapshot_stores_latest_report():
    output = CP6OutputSystem()

    report = make_report()

    output.build_snapshot(
        make_cycle(
            report=report
        )
    )

    assert output.last_report is report


def test_build_snapshot_stores_latest_snapshot():
    output = CP6OutputSystem()

    snapshot = output.build_snapshot(
        make_cycle(
            report=make_report()
        )
    )

    assert output.last_snapshot is snapshot


def test_render_contains_all_four_panels():
    output = CP6OutputSystem()

    text = output.render(
        make_cycle(
            report=make_report()
        ),
        clear=False,
    )

    assert "PANEL 1 — ENGINE / MARKET" in text
    assert "PANEL 2 — PULLBACK HUNTER" in text
    assert "PANEL 3 — SIGNAL / MONITOR" in text
    assert "PANEL 4 — 15-MINUTE CYCLE" in text


def test_render_contains_engine_title():
    output = CP6OutputSystem()

    text = output.render(
        make_cycle(),
        clear=False,
    )

    assert "PULLBACK ENGINE v1.0" in text
    assert "CP6 OUTPUT SYSTEM" in text


def test_render_does_not_modify_candidates():
    output = CP6OutputSystem()

    candidate = type(
        "Candidate",
        (),
        {
            "state": "QUALIFIED_PULLBACK",
            "symbol": "TEST",
            "direction": "LONG",
            "created_at": datetime(
                2026,
                9,
                12,
                10,
                0,
                tzinfo=IST,
            ),
            "setup_id": "TEST-001",
            "quality": {
                "impulse_atr_multiple": 2.0,
            },
        },
    )()

    cycle = make_cycle()
    cycle.candidates = [candidate]

    before = list(
        cycle.candidates
    )

    output.render(
        cycle,
        clear=False,
    )

    assert cycle.candidates == before


def test_render_does_not_modify_signals():
    signal = Signal(
        setup_id="TEST-002",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=datetime(
            2026,
            9,
            12,
            10,
            30,
            tzinfo=IST,
        ),
        entry_price=100.0,
        trend_invalidation_status=(
            "TREND_VALID_AT_TRIGGER"
        ),
    )

    cycle = make_cycle(
        signals=[signal]
    )

    before = list(
        cycle.new_signals
    )

    CP6OutputSystem().render(
        cycle,
        clear=False,
    )

    assert cycle.new_signals == before


def test_render_cycle_convenience_function():
    text = render_cycle(
        make_cycle(
            report=make_report()
        )
    )

    assert "PANEL 1 — ENGINE / MARKET" in text
    assert "PANEL 2 — PULLBACK HUNTER" in text
    assert "PANEL 3 — SIGNAL / MONITOR" in text
    assert "PANEL 4 — 15-MINUTE CYCLE" in text


def test_render_cycle_can_use_previous_report():
    previous = make_report()

    cycle = make_cycle(
        report=None
    )

    text = render_cycle(
        cycle,
        previous_report=previous,
    )

    assert "10:30 CYCLE" in text
    assert "Universe: 450" in text


def test_snapshot_contains_panel_strings():
    output = CP6OutputSystem()

    snapshot = output.build_snapshot(
        make_cycle(
            report=make_report()
        )
    )

    assert isinstance(
        snapshot.panel_1,
        str,
    )
    assert isinstance(
        snapshot.panel_2,
        str,
    )
    assert isinstance(
        snapshot.panel_3,
        str,
    )
    assert isinstance(
        snapshot.panel_4,
        str,
    )


def test_panel_4_preserves_zero_signals():
    report = make_report()

    report = CycleReport(
        timestamp=report.timestamp,
        universe=report.universe,
        currently_calculable=report.currently_calculable,
        temporarily_skipped=report.temporarily_skipped,
        developing=report.developing,
        qualified=report.qualified,
        armed=report.armed,
        new_signals=0,
        hunter_status=report.hunter_status,
        funnel=FunnelCounts(
            universe=450,
            price_eligible=300,
            valid_data=440,
            regime=25,
            impulse=18,
            pullback=12,
            structure=9,
            trend=8,
            early_entry=6,
            reacceleration=0,
            signals=0,
        ),
    )

    text = CP6OutputSystem.panel_4(
        report
    )

    assert "New signals: 0" in text
    assert "0 SIGNALS" in text
