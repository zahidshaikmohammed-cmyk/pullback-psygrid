import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

from pullback_engine.core import IST
from pullback_engine.dashboard import DASHBOARD_PORT, DashboardState, start_dashboard


@contextmanager
def running_dashboard(**kwargs):
    server = start_dashboard(DashboardState(), host="127.0.0.1", port=0, **kwargs)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_dashboard_contract_is_read_only_state_surface():
    state = DashboardState()
    snapshot = state.snapshot()
    assert snapshot["service"] == "PULLBACK_ENGINE_V1_0"
    assert snapshot["expected_stocks"] == 990
    assert snapshot["signals"] == []
    assert snapshot["events"] == []


def test_dashboard_state_waiting_is_market_aware():
    state = DashboardState()
    target = datetime(2026, 9, 14, 9, 15, tzinfo=IST)
    state.set_waiting(target)
    snapshot = state.snapshot()
    assert snapshot["engine"] == "WAITING"
    assert snapshot["session"] == "CLOSED"
    assert "09:15:00" in snapshot["events"][0]["message"]


def test_dashboard_port_is_separate_from_psygrid():
    assert DASHBOARD_PORT == 10001


def test_dashboard_reports_cp5_cycle_errors():
    state = DashboardState()
    cp2 = SimpleNamespace(healthy_stock_count=450, expected_stock_count=450, stale_stock_count=0)
    cp5 = SimpleNamespace(
        cp2_cycle=cp2,
        candidates=[],
        monitoring={},
        new_signals=[],
        errors=["worker:RuntimeError:boom"],
        report=None,
    )
    cycle = SimpleNamespace(
        cp5_cycle=cp5,
        timestamp=datetime(2026, 9, 15, 10, 0, tzinfo=IST),
        integration_errors=(),
        healthy=False,
    )
    state.update(cycle)
    assert state.snapshot()["runtime_errors"] == 1


def test_dashboard_is_open_when_no_token_is_configured():
    with running_dashboard() as base_url:
        status, body = _get(f"{base_url}/api/state")
        assert status == 200
        assert json.loads(body)["service"] == "PULLBACK_ENGINE_V1_0"


def test_dashboard_rejects_missing_token_when_configured():
    with running_dashboard(access_token="s3cret") as base_url:
        status, body = _get(f"{base_url}/api/state")
        assert status == 401
        assert json.loads(body)["error"] == "unauthorized"


def test_dashboard_rejects_wrong_token():
    with running_dashboard(access_token="s3cret") as base_url:
        status, _ = _get(f"{base_url}/api/state?token=wrong")
        assert status == 401


def test_dashboard_accepts_correct_token_via_query_param():
    with running_dashboard(access_token="s3cret") as base_url:
        status, body = _get(f"{base_url}/?token=s3cret")
        assert status == 200
        assert b"PULLBACK ENGINE" in body


def test_dashboard_accepts_correct_token_via_bearer_header():
    with running_dashboard(access_token="s3cret") as base_url:
        status, _ = _get(f"{base_url}/api/state", headers={"Authorization": "Bearer s3cret"})
        assert status == 200


def test_dashboard_health_never_requires_a_token():
    with running_dashboard(access_token="s3cret") as base_url:
        status, body = _get(f"{base_url}/health")
        assert status == 200
        assert json.loads(body)["status"] == "OK"
