#!/usr/bin/env python3

import asyncio
import json

from pullback_engine.cp2 import CP2DataEngine
from pullback_engine.cp3 import CP3PullbackHunter


async def main() -> None:
    data_engine = CP2DataEngine()
    hunter = CP3PullbackHunter()

    cp2_cycle = await data_engine.cycle()

    cp3_cycle = hunter.cycle(
        cp2_cycle,
        now=cp2_cycle.timestamp,
    )

    print(
        json.dumps(
            hunter.health(),
            indent=2,
            default=str,
        )
    )

    print("")

    for symbol, result in sorted(
        cp3_cycle.stock_results.items()
    ):
        print(
            f"{symbol}: "
            f"5m={len(result.candles_5m)} "
            f"candidates={len(result.candidates)} "
            f"errors={len(result.errors)}"
        )

        for candidate in result.candidates:
            print(
                f"  {candidate.setup_id} | "
                f"{candidate.direction} | "
                f"{candidate.state} | "
                f"close={candidate.current_close:.2f}"
            )

    print("")
    print(
        f"TOTAL CANDIDATES: "
        f"{len(cp3_cycle.candidates)}"
    )
    print(
        f"TRIGGER ARMED: "
        f"{len(cp3_cycle.armed_candidates)}"
    )


if __name__ == "__main__":
    asyncio.run(main())