import asyncio
from pullback_engine.engine import PullbackEngine
from pullback_engine.endpoints import FetchResult
from pullback_engine import engine as em

def test_fault_isolation_endpoint_failure(monkeypatch):
    async def fake_all(urls,timeout):
        return {
            "a":FetchResult("a",{"stocks":{"AAA":{"1m":[]}}},None,1),
            "b":FetchResult("b",None,"boom",1),
        }
    async def fake_nifty(url,timeout):
        return FetchResult("NIFTY",{"5m":[]},None,1)
    monkeypatch.setattr(em,"fetch_all",fake_all)
    monkeypatch.setattr(em,"fetch_nifty",fake_nifty)
    e=PullbackEngine(endpoint_urls={"a":"x","b":"y"},nifty_url="n")
    c=asyncio.run(e.cycle())
    assert "AAA" in c.stocks
    assert "b" in c.endpoint_errors
    assert e.health()["engine"]=="RUNNING"

def test_fault_isolation_bad_stock(monkeypatch):
    payload={"stocks":{
        "GOOD":{"1m":[{"timestamp":"2026-01-01T09:15:00+05:30","open":10,"high":11,"low":9,"close":10,"volume":1}]},
        "BAD":{"1m":[{"timestamp":"2026-01-01T09:15:00+05:30","open":10,"high":9,"low":9,"close":10,"volume":1}]}
    }}
    async def fake_all(urls,timeout): return {"a":FetchResult("a",payload,None,1)}
    async def fake_nifty(url,timeout): return FetchResult("NIFTY",{"5m":[]},None,1)
    monkeypatch.setattr(em,"fetch_all",fake_all); monkeypatch.setattr(em,"fetch_nifty",fake_nifty)
    e=PullbackEngine(endpoint_urls={"a":"x"},nifty_url="n")
    c=asyncio.run(e.cycle())
    assert c.stocks["GOOD"].alive
    assert c.stocks["BAD"].errors
    assert e.health()["engine"]=="RUNNING"
