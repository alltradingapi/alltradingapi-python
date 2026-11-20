"""Fetch the latest candles for an asset and print a small table.

Run:
    python examples/01_quickstart.py

Requires ATA_API_KEY.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alltradingapi import Client


def main() -> None:
    key = os.environ.get("ATA_API_KEY", "")
    if not key:
        print("Set ATA_API_KEY first.")
        sys.exit(1)

    with Client(api_key=key) as ata:
        assets = ata.assets(only_open=True)
        print("Open assets:", len(assets))
        if not assets:
            print("Nothing is open right now.")
            return

        symbol = assets[0].symbol
        candles = ata.candles(symbol, hours=1, timeframe="1m")
        print()
        print(symbol, "- last 5 candles (1m):")
        print(
            "{:<20} {:>9} {:>9} {:>9} {:>9}  {}".format(
                "time", "open", "high", "low", "close", "dir"
            )
        )
        for c in candles[-5:]:
            print(
                "{:<20} {:>9.5f} {:>9.5f} {:>9.5f} {:>9.5f}  {}".format(
                    (c.time_read or "")[:19] or str(c.time),
                    c.open,
                    c.high,
                    c.low,
                    c.close,
                    c.direction,
                )
            )

        saved = ata.saved(symbol, count=50)
        print()
        print("Saved candles returned:", saved.returned_count)


if __name__ == "__main__":
    main()
