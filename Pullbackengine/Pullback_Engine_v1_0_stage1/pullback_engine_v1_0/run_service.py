from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from pullback_engine.core import IST, MARKET_END, MARKET_START
from pullback_engine.cp7 import CP7IntegrationEngine
from pullback_engine.dashboard import DashboardState, start_dashboard


def _next_market_start(now: datetime) -> datetime:
    candidate = now.astimezone(IST).replace(
        hour=MARKET_START.hour,
        minute=MARKET_START.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now or candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _in_market_session(now: datetime) -> bool:
    local = now.astimezone(IST)
    return local.weekday() < 5 and MARKET_START <= local.time() <= MARKET_END


async def main() -> None:
    dashboard_state = DashboardState()
    current_engine: list[CP7IntegrationEngine | None] = [None]
    dashboard_token = os.environ.get("PULLBACK_ENGINE_DASHBOARD_TOKEN")
    dashboard = start_dashboard(
        dashboard_state, lambda: current_engine[0], access_token=dashboard_token
    )
    print(f"Pullback Engine dashboard listening on {dashboard.server_address}", flush=True)
    if not dashboard_token:
        print(
            "Pullback Engine dashboard: PULLBACK_ENGINE_DASHBOARD_TOKEN is not set — "
            "the dashboard is reachable by anyone who can reach this host/port.",
            flush=True,
        )
    while True:
        now = datetime.now(IST)
        if not _in_market_session(now):
            current_engine[0] = None
            target = _next_market_start(now)
            dashboard_state.set_waiting(target)
            delay = max(1.0, (target - now).total_seconds())
            print(f"Pullback Engine supervisor waiting for next market session: {target.isoformat()}", flush=True)
            await asyncio.sleep(min(delay, 60.0))
            continue

        engine = CP7IntegrationEngine()
        current_engine[0] = engine
        dashboard_state.set_entering()
        print("Pullback Engine supervisor entering market session", flush=True)
        try:
            await engine.run_forever()
        except Exception as exc:
            print(f"Pullback Engine supervisor cycle error: {type(exc).__name__}: {exc}", flush=True)
        finally:
            engine.stop()
            current_engine[0] = None
            print("Pullback Engine supervisor left market session", flush=True)

        await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
