from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .core import IST, MARKET_END, MARKET_START

DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 10001
HISTORY_LIMIT = 100
EXPECTED_STOCKS = 450


@dataclass
class DashboardState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    engine: str = "WAITING"
    session: str = "CLOSED"
    timestamp: datetime | None = None
    scanned_stocks: int = 0
    processed_stocks: int = 0
    processing_failures: int = 0
    healthy_stocks: int = 0
    expected_stocks: int = EXPECTED_STOCKS
    stale_stocks: int = 0
    candidates: int = 0
    developing: int = 0
    qualified: int = 0
    armed: int = 0
    active_monitors: int = 0
    new_signals: int = 0
    runtime_errors: int = 0
    integration_errors: int = 0
    last_cycle_healthy: bool = False
    last_report: dict[str, Any] | None = None
    candidates_top: list[dict[str, Any]] = field(default_factory=list)
    monitors: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_waiting(self, target: datetime) -> None:
        with self.lock:
            self.engine = "WAITING"
            self.session = "CLOSED"
            self._event(f"Waiting for market session: {target:%Y-%m-%d %H:%M:%S} IST")

    def set_entering(self) -> None:
        with self.lock:
            self.engine = "STARTING"
            self.session = "OPEN"
            self._event("Entering market session")

    def update(self, cycle: Any) -> None:
        cp5 = cycle.cp5_cycle
        cp2 = cp5.cp2_cycle
        with self.lock:
            self.engine = "RUNNING"
            self.session = "OPEN"
            self.timestamp = cycle.timestamp
            self.expected_stocks = int(getattr(cp2, "expected_stock_count", EXPECTED_STOCKS))
            self.scanned_stocks = int(len(getattr(cp2, "stocks", {}) or {}))
            self.processed_stocks = int(len(getattr(cp5, "stock_results", {}) or {}))
            self.processing_failures = max(0, self.scanned_stocks - self.processed_stocks)
            self.healthy_stocks = int(getattr(cp2, "healthy_stock_count", 0))
            self.stale_stocks = int(getattr(cp2, "stale_stock_count", 0))
            candidates = list(getattr(cp5, "candidates", []) or [])
            self.candidates = len(candidates)
            self.developing = sum(c.state == "IMPULSE_DETECTED" for c in candidates)
            self.qualified = sum(c.state == "QUALIFIED_PULLBACK" for c in candidates)
            self.armed = sum(c.state == "TRIGGER_ARMED" for c in candidates)
            monitoring = dict(getattr(cp5, "monitoring", {}) or {})
            self.active_monitors = sum(bool(getattr(s, "active", False)) for s in monitoring.values())
            signals = list(getattr(cp5, "new_signals", []) or [])
            self.new_signals = len(signals)
            cycle_errors = list(getattr(cp5, "errors", []) or [])
            self.runtime_errors = len(cycle_errors)
            self.integration_errors = len(getattr(cycle, "integration_errors", ()) or ())
            self.last_cycle_healthy = bool(getattr(cycle, "healthy", False))

            if self.scanned_stocks != self.expected_stocks or self.processed_stocks != self.expected_stocks:
                self._event(
                    f"SCAN COVERAGE WARNING — scheduled={self.scanned_stocks}/{self.expected_stocks}, "
                    f"processed={self.processed_stocks}/{self.expected_stocks}"
                )

            strongest = sorted(
                candidates,
                key=lambda c: float((getattr(c, "quality", {}) or {}).get("impulse_atr_multiple") or 0.0),
                reverse=True,
            )[:12]
            self.candidates_top = [
                {"symbol": c.symbol, "direction": c.direction, "state": c.state, "setup_id": c.setup_id}
                for c in strongest
            ]
            self.monitors = [
                {
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "status": s.status,
                    "setup_id": s.setup_id,
                    "active": bool(s.active),
                }
                for s in monitoring.values()
            ]
            for signal in signals:
                item = {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "entry_price": float(signal.entry_price),
                    "signal_timestamp": signal.signal_timestamp.isoformat(),
                    "setup_id": signal.setup_id,
                    "trend_invalidation_status": signal.trend_invalidation_status,
                }
                if not any(x.get("setup_id") == item["setup_id"] for x in self.signals):
                    self.signals.insert(0, item)
                    self._event(f"NEW {signal.direction} SIGNAL — {signal.symbol} @ ₹{signal.entry_price:.2f}")
                self.signals = self.signals[:HISTORY_LIMIT]
            report = getattr(cp5, "report", None)
            if report is not None:
                f = report.funnel
                self.last_report = {
                    "timestamp": report.timestamp.isoformat(),
                    "universe": report.universe,
                    "currently_calculable": report.currently_calculable,
                    "temporarily_skipped": report.temporarily_skipped,
                    "developing": report.developing,
                    "qualified": report.qualified,
                    "armed": report.armed,
                    "new_signals": report.new_signals,
                    "hunter_status": report.hunter_status,
                    "funnel": {
                        "universe": f.universe,
                        "price_eligible": f.price_eligible,
                        "valid_data": f.valid_data,
                        "impulse": f.impulse,
                        "pullback": f.pullback,
                        "structure": f.structure,
                        "trend": f.trend,
                        "early_entry": f.early_entry,
                        "reacceleration": f.reacceleration,
                        "signals": f.signals,
                    },
                }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "service": "PULLBACK_ENGINE_V1_0",
                "engine": self.engine,
                "session": self.session,
                "market_window": f"{MARKET_START.strftime('%H:%M')}–{MARKET_END.strftime('%H:%M')} IST",
                "timestamp": self.timestamp.isoformat() if self.timestamp else None,
                "scanned_stocks": self.scanned_stocks,
                "processed_stocks": self.processed_stocks,
                "processing_failures": self.processing_failures,
                "scan_complete": self.scanned_stocks == self.expected_stocks and self.processed_stocks == self.expected_stocks,
                "healthy_stocks": self.healthy_stocks,
                "expected_stocks": self.expected_stocks,
                "candidates": self.candidates,
                "developing": self.developing,
                "qualified": self.qualified,
                "armed": self.armed,
                "active_monitors": self.active_monitors,
                "new_signals": self.new_signals,
                "stale_stocks": self.stale_stocks,
                "runtime_errors": self.runtime_errors,
                "integration_errors": self.integration_errors,
                "last_cycle_healthy": self.last_cycle_healthy,
                "candidates_top": self.candidates_top,
                "monitors": self.monitors,
                "signals": self.signals,
                "last_report": self.last_report,
                "events": self.events,
                "server_time": datetime.now(IST).isoformat(),
            }

    def _event(self, message: str) -> None:
        self.events.insert(0, {"timestamp": datetime.now(IST).isoformat(), "message": message})
        self.events = self.events[:HISTORY_LIMIT]


class _Handler(BaseHTTPRequestHandler):
    state: DashboardState
    html: str

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html.encode("utf-8"))
        elif self.path in ("/api/state", "/api/status"):
            self._send(200, "application/json; charset=utf-8", json.dumps(self.state.snapshot(), separators=(",", ":")).encode("utf-8"))
        elif self.path == "/health":
            self._send(200, "application/json", b'{"service":"PULLBACK_ENGINE_V1_0_DASHBOARD","status":"OK"}')
        else:
            self._send(404, "application/json", b'{"error":"not_found"}')

    def log_message(self, format: str, *args: Any) -> None:
        return


def start_dashboard(state: DashboardState, engine_provider: Callable[[], Any] | None = None, host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> ThreadingHTTPServer:
    html_path = Path(__file__).resolve().parents[1] / "dashboard.html"
    html = html_path.read_text(encoding="utf-8")

    class Handler(_Handler):
        pass

    Handler.state = state
    Handler.html = html
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="pullback-dashboard", daemon=True).start()

    if engine_provider is not None:
        def observe() -> None:
            seen: Any = None
            while True:
                try:
                    engine = engine_provider()
                    cycle = getattr(engine, "last_cycle", None) if engine is not None else None
                    if cycle is not None and cycle is not seen:
                        state.update(cycle)
                        seen = cycle
                except Exception as exc:
                    with state.lock:
                        state.runtime_errors += 1
                        state._event(f"Dashboard observer error: {type(exc).__name__}: {exc}")
                threading.Event().wait(0.5)

        threading.Thread(target=observe, name="pullback-dashboard-observer", daemon=True).start()
    return server
