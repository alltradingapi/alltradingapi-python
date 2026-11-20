"""Chart screenshots, trade result checks, and a small async candle scan.

Run:
    python examples/03_charts_results_async.py

Requires ATA_API_KEY (chart image needs the screenshots product key when
using per-product keys).
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alltradingapi import AsyncClient


async def main_async() -> None:
    key = os.environ.get("ATA_API_KEY", "")
    if not key:
        print("Set ATA_API_KEY first.")
        sys.exit(1)

    async with AsyncClient(api_key=key, max_concurrency=6) as ata:
        # chart image for one asset
        png = await ata.screenshot("EURUSD_otc", timeframe="1m")
        with open("chart.png", "wb") as fh:
            fh.write(png)
        print(f"saved chart.png ({len(png)} bytes)")

        # was a call at 20:00 a win?
        outcome = await ata.result("EURUSD_otc", time="20:00", direction="call")
        if outcome.result is not None:
            print("call @ 20:00 ->", outcome.result.value)

        # candles for a few assets at once; failures stay per-asset
        results = await ata.scan(["EURUSD_otc", "GBPUSD_otc", "BRLUSD_otc"], hours=1, concurrency=3)
        for symbol, value in results.items():
            if isinstance(value, Exception):
                print(symbol, "failed:", type(value).__name__)
            else:
                print(symbol, "candles:", len(value))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
