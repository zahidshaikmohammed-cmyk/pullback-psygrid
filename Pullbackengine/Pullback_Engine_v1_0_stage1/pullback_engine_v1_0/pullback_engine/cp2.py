from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping
from urllib.request import Request, urlopen

from .core import Candle, IST, parse_timestamp, validate_candles

STOCK_SHARDS = tuple("abcdefghij")
EXPECTED_STOCKS_PER_SHARD = 45
EXPECTED_STOCKS = 450
NIFTY_NAME = "NIFTY"
DEFAULT_STALE_AFTER_SECONDS = 180.0

ENDPOINTS = {
    s: f"http://140.245.226.102:10000/public/live-{s}.json"
    for s in STOCK_SHARDS
}

NIFTY_URL = "http://140.245.226.102:10000/public/nifty.json"


@dataclass(frozen=True)
class TransportResult:
    name: str
    url: str
    payload: dict[str, Any] | None
    error: str | None
    elapsed_ms: float


@dataclass
class StockData:
    # Keep the original positional field order: CP3/CP4 tests and integration
    # code construct StockData positionally. New v4 metadata is appended.
    symbol: str
    endpoint: str
    candles_1m: list[Candle] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stale: bool = False
    healthy: bool = False
    last_timestamp: datetime | None = None
    feed_timestamp: datetime | None = None
    security_id: str | None = None
    previous_close: float | None = None
    today_open: float | None = None


@dataclass
class EndpointData:
    name: str
    url: str
    stock_count: int = 0
    symbols: list[str] = field(default_factory=list)
    stocks: dict[str, StockData] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: float = 0.0
    healthy: bool = False


@dataclass
class CP2Cycle:
    timestamp: datetime
    endpoints: dict[str, EndpointData]
    stocks: dict[str, StockData]
    nifty_payload: dict[str, Any] | None
    nifty_error: str | None
    expected_stock_count: int = EXPECTED_STOCKS

    @property
    def healthy_stock_count(self):
        return sum(s.healthy for s in self.stocks.values())

    @property
    def stale_stock_count(self):
        return sum(s.stale for s in self.stocks.values())

    @property
    def unique_stock_count(self):
        return len(self.stocks)


class CP2DataEngine:
    """CP2: concurrent A-J + NIFTY ingestion using Psygrid v4 stock payloads."""

    def __init__(
        self,
        endpoint_urls: Mapping[str, str] | None = None,
        nifty_url: str = NIFTY_URL,
        timeout_seconds: float = 8.0,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ):
        self.endpoint_urls = dict(endpoint_urls or ENDPOINTS)
        self.nifty_url = nifty_url
        self.timeout_seconds = timeout_seconds
        self.stale_after_seconds = stale_after_seconds
        self.running = True
        self.last_cycle: CP2Cycle | None = None

    @staticmethod
    def _fetch(name, url, timeout):
        started = time.perf_counter()
        try:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "User-Agent": "Psygrid-Pullback-Engine/1.0",
                },
            )
            with urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("endpoint JSON root must be an object")
            return TransportResult(
                name, url, payload, None,
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return TransportResult(
                name, url, None, f"{type(exc).__name__}: {exc}",
                (time.perf_counter() - started) * 1000,
            )

    async def _fetch_all(self):
        jobs = [
            asyncio.to_thread(self._fetch, name, url, self.timeout_seconds)
            for name, url in self.endpoint_urls.items()
        ]
        jobs.append(
            asyncio.to_thread(
                self._fetch, NIFTY_NAME, self.nifty_url, self.timeout_seconds
            )
        )
        return await asyncio.gather(*jobs)

    def _stock(self, endpoint, symbol, item, now):
        stock = StockData(symbol=symbol, endpoint=endpoint)
        try:
            if not isinstance(item, dict):
                raise ValueError("stock payload must be an object")

            stock.security_id = (
                str(item["security_id"])
                if item.get("security_id") is not None
                else None
            )

            for field_name in ("previous_close", "today_open"):
                value = item.get(field_name)
                if value is None:
                    stock.errors.append(f"missing_{field_name}")
                else:
                    try:
                        value = float(value)
                    except (TypeError, ValueError) as exc:
                        stock.errors.append(
                            f"{field_name}:{type(exc).__name__}:{exc}"
                        )
                        value = None
                    setattr(stock, field_name, value)

            # LIVE PSYGRID V4 CONTRACT:
            #   stocks[SYMBOL].candles_1m = [...]
            # The old engine incorrectly read `1m`, which is not the live
            # endpoint field and caused valid live candles to be rejected.
            rows = item.get("candles_1m")
            if not isinstance(rows, list):
                raise ValueError("missing candles_1m candle list")

            valid, errors = validate_candles(rows)
            stock.candles_1m = valid
            stock.errors.extend(errors)

            if not valid:
                stock.errors.append("no_valid_candles_1m")
                return stock

            stock.last_timestamp = valid[-1].timestamp

            # v4 normally has no ltp_timestamp; when present, it is an
            # additional live freshness signal. Otherwise the latest candle
            # is the freshness timestamp.
            raw_ltp_timestamp = item.get("ltp_timestamp")
            if raw_ltp_timestamp is not None:
                try:
                    stock.feed_timestamp = parse_timestamp(raw_ltp_timestamp)
                except Exception as exc:
                    stock.errors.append(
                        f"ltp_timestamp:{type(exc).__name__}:{exc}"
                    )

            freshness_timestamp = max(
                ts
                for ts in (stock.last_timestamp, stock.feed_timestamp)
                if ts is not None
            )
            age = max(0.0, (now - freshness_timestamp).total_seconds())
            stock.stale = age > self.stale_after_seconds
            if stock.stale:
                stock.errors.append(f"stale:{age:.1f}s")

            # Metadata is diagnostic; OHLCV validity and freshness determine
            # health so a malformed optional field cannot kill a live stock.
            stock.healthy = not stock.stale and bool(valid)
        except Exception as exc:
            stock.errors.append(f"stock:{type(exc).__name__}:{exc}")
        return stock

    def _endpoint(self, result, now):
        endpoint = EndpointData(
            result.name,
            result.url,
            elapsed_ms=result.elapsed_ms,
        )
        if result.payload is None:
            endpoint.error = result.error or "unknown endpoint error"
            return endpoint

        stocks = result.payload.get("stocks")
        if not isinstance(stocks, dict):
            endpoint.error = "missing_stocks_object"
            return endpoint

        endpoint.symbols = [str(symbol).upper() for symbol in stocks]
        endpoint.stock_count = len(endpoint.symbols)
        if endpoint.stock_count != EXPECTED_STOCKS_PER_SHARD:
            endpoint.error = (
                f"universe_count:{endpoint.stock_count}!="
                f"{EXPECTED_STOCKS_PER_SHARD}"
            )

        for raw_symbol, item in stocks.items():
            symbol = str(raw_symbol).upper()
            if symbol in endpoint.stocks:
                endpoint.stocks[symbol].healthy = False
                endpoint.stocks[symbol].errors.append(
                    "duplicate_symbol_within_endpoint"
                )
                continue
            endpoint.stocks[symbol] = self._stock(
                result.name, symbol, item, now
            )

        endpoint.healthy = endpoint.error is None
        return endpoint

    @staticmethod
    def _check_nifty(payload):
        if payload is None:
            return "NIFTY payload unavailable"
        if str(payload.get("symbol", "")).upper() != "NIFTY":
            return "NIFTY symbol mismatch"
        if str(payload.get("security_id", "")) != "13":
            return "NIFTY security_id mismatch"
        if str(payload.get("exchange_segment", "")) != "IDX_I":
            return "NIFTY exchange_segment mismatch"
        if not isinstance(payload.get("5m"), list):
            return "NIFTY 5m candle list unavailable"
        return None

    async def cycle(self, now: datetime | None = None):
        now = (now or datetime.now(IST)).astimezone(IST)
        results = await self._fetch_all()
        endpoints = {}
        nifty_payload = None
        nifty_error = None

        for result in results:
            if result.name == NIFTY_NAME:
                nifty_payload = result.payload
                nifty_error = result.error or self._check_nifty(result.payload)
                continue
            endpoints[result.name] = self._endpoint(result, now)

        stocks = {}
        for name in STOCK_SHARDS:
            endpoint = endpoints.get(name)
            if not endpoint:
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

        self.last_cycle = CP2Cycle(
            now, endpoints, stocks, nifty_payload, nifty_error
        )
        return self.last_cycle

    def health(self):
        cycle = self.last_cycle
        return {
            "engine": "RUNNING" if self.running else "STOPPED",
            "expected_stocks": EXPECTED_STOCKS,
            "unique_stocks": cycle.unique_stock_count if cycle else 0,
            "healthy_stocks": cycle.healthy_stock_count if cycle else 0,
            "stale_stocks": cycle.stale_stock_count if cycle else 0,
            "endpoint_failures": (
                [k for k, v in cycle.endpoints.items() if v.error]
                if cycle else list(STOCK_SHARDS)
            ),
            "endpoint_counts": (
                {k: v.stock_count for k, v in cycle.endpoints.items()}
                if cycle else {}
            ),
            "nifty_available": bool(
                cycle
                and cycle.nifty_payload is not None
                and cycle.nifty_error is None
            ),
            "nifty_error": cycle.nifty_error if cycle else None,
        }
