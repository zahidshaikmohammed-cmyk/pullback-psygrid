from __future__ import annotations
import asyncio, json, time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.request import Request, urlopen


ENDPOINTS = {x:f"http://140.245.226.102:10000/public/live-{x}.json" for x in "abcdefghij"}
NIFTY_URL="http://140.245.226.102:10000/public/nifty.json"


@dataclass
class FetchResult:
    name:str
    payload:Optional[dict]
    error:Optional[str]
    elapsed_ms:float


def fetch_json_sync(name:str,url:str,timeout:float=10.0)->FetchResult:
    t=time.perf_counter()
    try:
        req=Request(url,headers={"Accept":"application/json","Cache-Control":"no-cache"})
        with urlopen(req,timeout=timeout) as r:
            raw=r.read()
        return FetchResult(name,json.loads(raw.decode("utf-8")),None,(time.perf_counter()-t)*1000)
    except Exception as exc:
        return FetchResult(name,None,f"{type(exc).__name__}: {exc}",(time.perf_counter()-t)*1000)


async def fetch_all(urls:dict[str,str]=ENDPOINTS,timeout:float=10.0)->dict[str,FetchResult]:
    tasks=[asyncio.to_thread(fetch_json_sync,name,url,timeout) for name,url in urls.items()]
    return {r.name:r for r in await asyncio.gather(*tasks)}


async def fetch_nifty(url:str=NIFTY_URL,timeout:float=10.0)->FetchResult:
    return await asyncio.to_thread(fetch_json_sync,"NIFTY",url,timeout)


def extract_stock_candles(payload:dict)->dict[str,list[dict]]:
    stocks=payload.get("stocks",{})
    out={}
    if isinstance(stocks,dict):
        for symbol,item in stocks.items():
            if isinstance(item,dict):
                rows=item.get("1m",[])
                if isinstance(rows,list): out[str(symbol)]=rows
    return out


def extract_nifty_5m(payload:dict)->list[dict]:
    rows=payload.get("5m",[])
    return rows if isinstance(rows,list) else []
