from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .core import *
from .endpoints import ENDPOINTS, NIFTY_URL, fetch_all, fetch_nifty, extract_stock_candles, extract_nifty_5m


@dataclass
class StockSnapshot:
    symbol:str
    candles_1m:list[Candle]=field(default_factory=list)
    candles_5m:list[Candle]=field(default_factory=list)
    errors:list[str]=field(default_factory=list)
    alive:bool=False


@dataclass
class EngineCycle:
    timestamp:datetime
    stocks:dict[str,StockSnapshot]
    nifty_5m:list[Candle]
    endpoint_errors:dict[str,str]
    nifty_error:Optional[str]=None


class PullbackEngine:
    """Fault-isolated cycle engine. Strategy math lives in core.py."""
    def __init__(self, endpoint_urls=None, nifty_url=NIFTY_URL, fetch_timeout=10.0):
        self.endpoint_urls=endpoint_urls or ENDPOINTS
        self.nifty_url=nifty_url
        self.fetch_timeout=fetch_timeout
        self.states=StateStore()
        self.running=True
        self.last_cycle:Optional[EngineCycle]=None
        self._seq={}

    async def cycle(self, now:Optional[datetime]=None)->EngineCycle:
        now=now or datetime.now(IST)
        results=await fetch_all(self.endpoint_urls,self.fetch_timeout)
        nifty=await fetch_nifty(self.nifty_url,self.fetch_timeout)
        stocks={}
        for name,res in results.items():
            if res.payload is None:
                continue
            for symbol,rows in extract_stock_candles(res.payload).items():
                try:
                    valid,errors=validate_candles(rows)
                    five=aggregate_5m(valid)
                    stocks[symbol]=StockSnapshot(symbol,valid,five,errors,True)
                except Exception as exc:
                    # stock-local boundary: one stock never escapes into global cycle
                    stocks[symbol]=StockSnapshot(symbol,[],[],[f"processing:{type(exc).__name__}:{exc}"],False)
        nifty5=[]
        nifty_error=nifty.error
        if nifty.payload:
            try:
                # NIFTY endpoint supplies native 5m; the locked engine only consumes completed bars.
                valid,_=validate_candles(extract_nifty_5m(nifty.payload))
                nifty5=valid
            except Exception as exc:
                nifty_error=f"processing:{type(exc).__name__}:{exc}"
        endpoint_errors={k:v.error for k,v in results.items() if v.error}
        self.last_cycle=EngineCycle(now,stocks,nifty5,endpoint_errors,nifty_error)
        return self.last_cycle

    def health(self)->dict:
        c=self.last_cycle
        return {
            "engine":"RUNNING" if self.running else "STOPPED",
            "stocks_seen":len(c.stocks) if c else 0,
            "stocks_alive":sum(1 for x in c.stocks.values() if x.alive) if c else 0,
            "endpoint_failures":dict(c.endpoint_errors) if c else {},
            "nifty_available":bool(c and c.nifty_5m),
            "hunt_active":bool(c and c.timestamp.astimezone(IST).time()>=HUNT_START and c.timestamp.astimezone(IST).time()<MARKET_END),
        }
