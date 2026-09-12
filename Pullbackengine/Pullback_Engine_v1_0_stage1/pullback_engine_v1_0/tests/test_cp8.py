from datetime import datetime

import pytest

from pullback_engine.core import IST
from pullback_engine.cp8 import CP8AuditReport, CP8ParityAudit, run_cp8_audit


@pytest.fixture(scope="module")
def report() -> CP8AuditReport:
    return run_cp8_audit(datetime(2026, 9, 13, 10, 0, tzinfo=IST))


def test_cp8_report_is_timezone_aware(report):
    assert report.timestamp.tzinfo is not None


def test_cp8_covers_all_required_modules(report):
    assert report.source_scope == ("core.py", "cp2.py", "cp3.py", "cp4.py", "cp5.py", "cp6.py", "cp7.py")


def test_cp8_has_real_checks(report):
    assert len(report.checks) >= 20
    assert report.passed_count == len(report.checks)


def test_cp8_final_audit_passes(report):
    assert report.passed, [(c.name, c.detail) for c in report.failed]


def test_cp8_summary_is_machine_readable(report):
    assert report.summary().startswith("CP8 PASS:")


def test_cp8_has_no_failed_checks(report):
    assert report.failed == ()


def test_cp8_check_names_are_unique(report):
    names = [c.name for c in report.checks]
    assert len(names) == len(set(names))


def test_cp8_timestamp_normalizes_to_ist():
    report = run_cp8_audit(datetime(2026, 9, 13, 10, 0, tzinfo=IST))
    assert report.timestamp.tzinfo is not None
    assert report.passed


def test_cp8_does_not_create_signals_or_market_state():
    audit = CP8ParityAudit(datetime(2026, 9, 13, 10, 0, tzinfo=IST))
    assert not hasattr(audit, "engine")
    assert not hasattr(audit, "state_store")


def test_cp8_is_repeatable():
    a = run_cp8_audit(datetime(2026, 9, 13, 10, 0, tzinfo=IST))
    b = run_cp8_audit(datetime(2026, 9, 13, 10, 0, tzinfo=IST))
    assert [(x.name, x.passed) for x in a.checks] == [(x.name, x.passed) for x in b.checks]


def test_cp8_detects_failed_contract_without_hiding_it():
    check = CP8ParityAudit._check("intentional_failure", lambda: False)
    assert check.passed is False
    assert "false" in check.detail
