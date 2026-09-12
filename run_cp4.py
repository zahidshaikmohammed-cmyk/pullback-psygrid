#!/usr/bin/env python3

import asyncio
import json

from pullback_engine.cp2 import CP2DataEngine
from pullback_engine.cp4 import CP4TriggerEngine


async def main() -> None:
    data_engine = CP2DataEngine()
    trigger_engine = CP4TriggerEngine()

    cp2_cycle = await data_engine.cycle()

    cp4_cycle = trigger_engine.cycle(
        cp2_cycle,
        now=cp2_cycle.timestamp,
    )

    print(
        json.dumps(
            trigger_engine.health(),
            indent=2,
            default=str,
        )
    )

    print("")

    for symbol, result in sorted(
        cp4_cycle.stock_results.items()
    ):
        print(
            f"{symbol}: "
            f"signals={len(result.signals)} "
            f"errors={len(result.errors)}"
        )

        for signal in result.signals:
            print(
                f"  SIGNAL | "
                f"{signal.setup_id} | "
                f"{signal.direction} | "
                f"{signal.signal_timestamp} | "
                f"entry={signal.entry_price:.2f} | "
                f"{signal.trend_invalidation_status}"
            )

    print("")
    print(
        f"TOTAL NEW SIGNALS: "
        f"{len(cp4_cycle.signals)}"
    )


if __name__ == "__main__":
    asyncio.run(main())