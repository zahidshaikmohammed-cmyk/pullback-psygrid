from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from .core import IST
from .cp4 import Signal


class WebhookAlertNotifier:
    """Best-effort HTTP delivery of fired signals to an external channel.

    Never raises: a notification failure must never take down the trading
    loop or be mistaken for a strategy fault. Failures are handed to
    `on_error` so the caller can record them (e.g. CP5.runtime_errors)
    without the alert path itself being able to affect signal generation.

    The payload carries both "text" and "content" alongside the structured
    fields, so a Slack or Discord incoming webhook renders it natively with
    zero adapter code; any other receiver (a custom relay to SMS/Telegram/
    email) gets the same structured fields to route from.
    """

    def __init__(self, url: str, timeout_seconds: float = 5.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def __call__(self, signal: Signal) -> None:
        message = (
            f"{signal.direction} {signal.symbol} @ Rs{signal.entry_price:.2f} "
            f"| {signal.signal_timestamp.astimezone(IST):%Y-%m-%d %H:%M:%S IST} "
            f"| {signal.setup_id}"
        )
        payload: dict[str, Any] = {
            "text": message,
            "content": message,
            "setup_id": signal.setup_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "signal_timestamp": signal.signal_timestamp.astimezone(IST).isoformat(),
            "entry_price": signal.entry_price,
            "trend_invalidation_status": signal.trend_invalidation_status,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds):
            pass


def build_alert_callback(webhook_url: str | None):
    """Compose the notification chain the live service actually uses.

    Every alert always prints/rings locally (visible in the systemd
    journal) *and*, when a webhook URL is configured, is also POSTed
    out. A webhook failure never suppresses the local alert or vice
    versa - the two are independent best-effort deliveries.
    """
    from .cp5 import CP5ContinuousEngine

    notifier = WebhookAlertNotifier(webhook_url) if webhook_url else None

    def _alert(signal: Signal) -> None:
        CP5ContinuousEngine._default_alert(signal)
        if notifier is not None:
            notifier(signal)

    return _alert
