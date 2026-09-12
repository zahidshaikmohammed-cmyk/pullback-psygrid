from __future__ import annotations

import asyncio

from pullback_engine.cp5 import CP5ContinuousEngine
from pullback_engine.cp6 import CP6OutputSystem


async def main() -> None:
    engine = CP5ContinuousEngine()
    output = CP6OutputSystem()

    print("=" * 60)
    print("PULLBACK ENGINE v1.0 — CP6 OUTPUT SYSTEM")
    print("=" * 60)
    print("Starting CP5 engine + CP6 output layer...")
    print("")

    try:
        engine.running = True

        while engine.running:
            cycle = await engine.cycle_once()

            try:
                print(
                    output.render(
                        cycle,
                        clear=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    "CP6 OUTPUT ERROR "
                    "(engine continues): "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

            now = cycle.timestamp

            from datetime import timedelta

            next_minute = (
                now.replace(
                    second=0,
                    microsecond=0,
                )
                + timedelta(minutes=1)
            )

            delay = (
                next_minute - now
            ).total_seconds()

            await asyncio.sleep(
                max(0.1, delay)
            )

    except KeyboardInterrupt:
        print("\nCP6 stopped by user.")
        engine.stop()

    finally:
        print("")
        print("=" * 60)
        print("CP6 OUTPUT SYSTEM STOPPED")
        print("=" * 60)
        print(engine.health())


if __name__ == "__main__":
    asyncio.run(main())