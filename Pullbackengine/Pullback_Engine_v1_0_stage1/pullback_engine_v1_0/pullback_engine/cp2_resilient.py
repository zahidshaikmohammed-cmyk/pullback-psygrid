from __future__ import annotations

import asyncio
from datetime import datetime

from .cp2 import CP2DataEngine, CP2Cycle, EXPECTED_STOCKS, STOCK_SHARDS


class ResilientCP2DataEngine(CP2DataEngine):
    """CP2 transport supervisor.

    A live shard can briefly expose a partial JSON snapshot while its producer
    is refreshing.  A single partial read must never become the engine's
    universe for the minute.  Retry incomplete shard snapshots before handing
    the cycle to CP5.  No strategy logic is changed here.
    """

    MAX_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 0.20

    @staticmethod
    def _coverage_complete(cycle: CP2Cycle) -> bool:
        if cycle.unique_stock_count != EXPECTED_STOCKS:
            return False
        if set(cycle.endpoints) != set(STOCK_SHARDS):
            return False
        return all(
            endpoint.stock_count == 45 and endpoint.error is None
            for endpoint in cycle.endpoints.values()
        )

    async def cycle(self, now: datetime | None = None) -> CP2Cycle:
        last_cycle: CP2Cycle | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            last_cycle = await super().cycle(now=now)
            if self._coverage_complete(last_cycle):
                return last_cycle
            if attempt < self.MAX_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
        return last_cycle
