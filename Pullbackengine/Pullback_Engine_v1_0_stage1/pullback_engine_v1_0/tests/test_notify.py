from __future__ import annotations

import json
from datetime import datetime

from pullback_engine.core import IST
from pullback_engine.cp4 import Signal
from pullback_engine.notify import WebhookAlertNotifier, build_alert_callback


def make_signal() -> Signal:
    return Signal(
        setup_id="TEST-PB-20260920-001",
        symbol="TEST",
        direction="LONG",
        signal_timestamp=datetime(2026, 9, 20, 10, 5, tzinfo=IST),
        entry_price=101.5,
        trend_invalidation_status="TREND_VALID_AT_TRIGGER",
    )


def test_webhook_notifier_posts_json_with_text_and_content(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("pullback_engine.notify.urlopen", fake_urlopen)

    notifier = WebhookAlertNotifier("https://hooks.example.com/incoming")
    notifier(make_signal())

    assert captured["url"] == "https://hooks.example.com/incoming"
    assert captured["body"]["text"] == captured["body"]["content"]
    assert "TEST" in captured["body"]["text"]
    assert captured["body"]["setup_id"] == "TEST-PB-20260920-001"
    assert captured["body"]["entry_price"] == 101.5


def test_webhook_notifier_failure_propagates_to_caller(monkeypatch):
    def fake_urlopen(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("pullback_engine.notify.urlopen", fake_urlopen)

    notifier = WebhookAlertNotifier("https://hooks.example.com/incoming")
    try:
        notifier(make_signal())
        assert False, "expected OSError to propagate"
    except OSError:
        pass


def test_build_alert_callback_always_runs_local_alert(monkeypatch, capsys):
    callback = build_alert_callback(webhook_url=None)
    callback(make_signal())
    out = capsys.readouterr().out
    assert "ALERT" in out
    assert "TEST" in out


def test_build_alert_callback_also_posts_when_webhook_configured(monkeypatch, capsys):
    posted = []
    monkeypatch.setattr(
        "pullback_engine.notify.WebhookAlertNotifier.__call__",
        lambda self, signal: posted.append(signal.setup_id),
    )

    callback = build_alert_callback(webhook_url="https://hooks.example.com/incoming")
    callback(make_signal())

    out = capsys.readouterr().out
    assert "ALERT" in out
    assert posted == ["TEST-PB-20260920-001"]


def test_build_alert_callback_webhook_failure_does_not_block_local_alert(monkeypatch, capsys):
    def boom(self, signal):
        raise OSError("timeout")

    monkeypatch.setattr("pullback_engine.notify.WebhookAlertNotifier.__call__", boom)

    callback = build_alert_callback(webhook_url="https://hooks.example.com/incoming")
    try:
        callback(make_signal())
    except OSError:
        pass

    out = capsys.readouterr().out
    assert "ALERT" in out
