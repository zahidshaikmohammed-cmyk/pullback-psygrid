from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta

from pullback_engine.core import IST, MARKET_END, MARKET_START
from pullback_engine.cp7 import CP7IntegrationEngine


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
    while True:
        now = datetime.now(IST)
        if not _in_market_session(now):
            target = _next_market_start(now)
            delay = max(1.0, (target - now).total_seconds())
            print(
                f"Pullback Engine supervisor waiting for next market session: {target.isoformat()}",
                flush=True,
            )
            await asyncio.sleep(min(delay, 60.0))
            continue

        engine = CP7IntegrationEngine()
        print("Pullback Engine supervisor entering market session", flush=True)
        try:
            await engine.run_forever()
        except Exception as exc:
            print(f"Pullback Engine supervisor cycle error: {type(exc).__name__}: {exc}", flush=True)
        finally:
            engine.stop()
            print("Pullback Engine supervisor left market session", flush=True)

        await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
