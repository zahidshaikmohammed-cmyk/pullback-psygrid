from __future__ import annotations

import asyncio

from pullback_engine.cp7 import CP7IntegrationEngine


async def main() -> None:
    engine = CP7IntegrationEngine()
    print("=" * 64)
    print("PULLBACK ENGINE v1.0 — CP7 INTEGRATION SUPERVISOR")
    print("=" * 64)
    print("CP7 starting: CP5 runtime + CP6 output, one canonical loop")
    print("Universe: 450 stocks | Hunter: 10:00 IST | Market end: 15:15 IST")
    print("")
    try:
        await engine.run_forever()
    except KeyboardInterrupt:
        print("\nCP7 stopped by user.")
        engine.stop()
    except Exception as exc:
        print(f"\nCP7 launcher error: {type(exc).__name__}: {exc}", flush=True)
        engine.stop()
        raise
    finally:
        print("")
        print("=" * 64)
        print("CP7 INTEGRATION SUPERVISOR STOPPED")
        print("=" * 64)
        print(engine.health())


if __name__ == "__main__":
    asyncio.run(main())
