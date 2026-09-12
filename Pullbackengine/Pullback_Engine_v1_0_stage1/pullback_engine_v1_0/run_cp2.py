#!/usr/bin/env python3

import asyncio
import json

from pullback_engine.cp2 import CP2DataEngine


async def main():
    engine = CP2DataEngine()

    cycle = await engine.cycle()

    print(
        json.dumps(
            engine.health(),
            indent=2,
            default=str,
        )
    )

    for name, endpoint in sorted(
        cycle.endpoints.items()
    ):
        print(
            f"{name.upper()}: "
            f"stocks={endpoint.stock_count} "
            f"healthy={endpoint.healthy} "
            f"error={endpoint.error}"
        )

    print(
        "NIFTY: "
        f"available="
        f"{cycle.nifty_error is None and cycle.nifty_payload is not None} "
        f"error={cycle.nifty_error}"
    )


if __name__ == "__main__":
    asyncio.run(main())