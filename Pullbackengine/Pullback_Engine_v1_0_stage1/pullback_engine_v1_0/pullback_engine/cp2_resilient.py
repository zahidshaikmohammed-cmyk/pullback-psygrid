from __future__ import annotations

import asyncio
from datetime import datetime

from .core import IST
from .cp2 import CP2DataEngine, CP2Cycle


class ResilientCP2DataEngine(CP2DataEngine):
    """CP2 transport supervisor with bounded snapshot recovery.

    The full universe now comes from a single combined endpoint. That feed
    can briefly expose a partial snapshot or a transient HTTP failure while
    it refreshes, so the transport is retried before the cycle is handed to
    CP5, and the most complete attempt is kept. This recovers the full
    universe without inventing or caching stock data and without changing
    strategy mathematics.
    """

    MAX_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 0.20

    @staticmethod
    def _coverage_complete(cycle: CP2Cycle) -> bool:
        if cycle.unique_stock_count != cycle.expected_stock_count:
            return False
        return bool(cycle.endpoints) and all(
            endpoint.error is None for endpoint in cycle.endpoints.values()
        )

    @classmethod
    def _merge_attempts(
        cls,
        attempts: list[CP2Cycle],
        now: datetime,
    ) -> CP2Cycle:
        """Keep the most complete successful attempt across retries."""
        best = max(attempts, key=lambda c: c.unique_stock_count)
        endpoints = best.endpoints
        stocks = best.stocks

        nifty_payload = None
        nifty_error = None
        for cycle in reversed(attempts):
            if cycle.nifty_payload is not None and cycle.nifty_error is None:
                nifty_payload = cycle.nifty_payload
                nifty_error = None
                break
            if nifty_payload is None:
                nifty_payload = cycle.nifty_payload
                nifty_error = cycle.nifty_error

        return CP2Cycle(
            timestamp=now,
            endpoints=endpoints,
            stocks=stocks,
            nifty_payload=nifty_payload,
            nifty_error=nifty_error,
            expected_stock_count=best.expected_stock_count,
        )

    async def cycle(self, now: datetime | None = None) -> CP2Cycle:
        cycle_now = (now or datetime.now(IST)).astimezone(IST)
        attempts: list[CP2Cycle] = []

        for attempt in range(self.MAX_ATTEMPTS):
            current = await super().cycle(now=cycle_now)
            attempts.append(current)

            merged = self._merge_attempts(attempts, cycle_now)
            if self._coverage_complete(merged):
                self.last_cycle = merged
                return merged

            if attempt + 1 < self.MAX_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)

        merged = self._merge_attempts(attempts, cycle_now)
        self.last_cycle = merged
        return merged
