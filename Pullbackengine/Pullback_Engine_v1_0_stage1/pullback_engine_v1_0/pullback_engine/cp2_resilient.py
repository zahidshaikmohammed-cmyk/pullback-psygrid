from __future__ import annotations

import asyncio
from datetime import datetime

from .core import IST
from .cp2 import CP2DataEngine, CP2Cycle, EndpointData, EXPECTED_STOCKS, STOCK_SHARDS


class ResilientCP2DataEngine(CP2DataEngine):
    """CP2 transport supervisor with bounded snapshot recovery.

    A live shard can briefly expose a partial snapshot or a transient HTTP
    failure while its producer is refreshing. Retry the transport before the
    cycle is handed to CP5. When different retries succeed for different
    shards, combine the successful shard snapshots from those attempts. This
    recovers the full universe without inventing or caching stock data and
    without changing strategy mathematics.
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

    @classmethod
    def _merge_attempts(
        cls,
        attempts: list[CP2Cycle],
        now: datetime,
    ) -> CP2Cycle:
        """Use the newest successful snapshot for each shard."""
        selected: dict[str, EndpointData] = {}

        for cycle in attempts:
            for shard in STOCK_SHARDS:
                endpoint = cycle.endpoints.get(shard)
                if endpoint is None:
                    continue
                if endpoint.stock_count == 45 and endpoint.error is None:
                    selected[shard] = endpoint
                elif shard not in selected:
                    selected[shard] = endpoint

        stocks = {}
        for shard in STOCK_SHARDS:
            endpoint = selected.get(shard)
            if endpoint is None:
                continue
            for symbol, stock in endpoint.stocks.items():
                if symbol in stocks:
                    stocks[symbol].healthy = False
                    stock.healthy = False
                    stocks[symbol].errors.append(
                        f"duplicate_symbol_across_endpoints:{symbol}"
                    )
                    stock.errors.append(
                        f"duplicate_symbol_across_endpoints:{symbol}"
                    )
                else:
                    stocks[symbol] = stock

        # Keep compatibility with deterministic test/fallback cycles that
        # already carry their assembled stock map. Live CP2 cycles normally
        # populate endpoint.stocks, so this does not alter production data.
        if not stocks and attempts:
            latest_stocks = attempts[-1].stocks
            stocks = dict(latest_stocks)

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
            endpoints=selected,
            stocks=stocks,
            nifty_payload=nifty_payload,
            nifty_error=nifty_error,
            expected_stock_count=EXPECTED_STOCKS,
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
