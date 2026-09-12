from pullback_engine.cp8 import run_cp8_audit


if __name__ == "__main__":
    report = run_cp8_audit()
    print(report.summary())
    for check in report.checks:
        print(f"{'PASS' if check.passed else 'FAIL'} | {check.name} | {check.detail}")
    if not report.passed:
        raise SystemExit(1)
