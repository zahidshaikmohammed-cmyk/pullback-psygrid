#!/usr/bin/env python3
"""Run the locked Pullback Engine against the ten supplied stock endpoints.

This is the transport/ingestion harness. It deliberately does not invent
signals when data are unavailable. The full candidate/trigger orchestration
is the next integration layer; core mathematics is in pullback_engine/core.py.
"""
import asyncio, json
from pullback_engine.engine import PullbackEngine

async def main():
    engine=PullbackEngine()
    while True:
        cycle=await engine.cycle()
        print(json.dumps(engine.health(),default=str))
        await asyncio.sleep(60)

if __name__=="__main__":
    asyncio.run(main())
