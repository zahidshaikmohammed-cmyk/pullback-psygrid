from __future__ import annotations

import asyncio

from pullback_engine.cp5 import CP5ContinuousEngine


async def main() -> None:
    engine = CP5ContinuousEngine()

    print("========================================")
    print("PULLBACK ENGINE v1.0 — CP5 RUNTIME")
    print("========================================")
    print("CP5 engine starting...")
    print("Hunter: continuous 1-minute cycles")
    print("Candidate creation: 10:00 IST")
    print("Market end: 15:15 IST")
    print("Universe: 450 stocks")
    print("")

    try:
        await engine.run_forever()
    except KeyboardInterrupt:
        print("\nCP5 stopped by user.")
        engine.stop()
    except Exception as exc:
        print(
            f"\nCP5 launcher error: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        engine.stop()
        raise
    finally:
        print("")
        print("========================================")
        print("CP5 RUNTIME STOPPED")
        print("========================================")
        print(engine.health())


if __name__ == "__main__":
    asyncio.run(main())