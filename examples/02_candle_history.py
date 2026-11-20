"""Candle history with a tiny ASCII sparkline of the closes.

Run:
    python examples/02_candle_history.py

Requires ATA_API_KEY.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alltradingapi import Client

BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list) -> str:
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    return "".join(BLOCKS[min(7, int((v - low) / span * 7))] for v in values)


def main() -> None:
    key = os.environ.get("ATA_API_KEY", "")
    if not key:
        print("Set ATA_API_KEY first.")
        sys.exit(1)

    with Client(api_key=key) as ata:
        current = ata.candles("EURUSD_otc", hours=1, timeframe="1m")
        history = ata.history("EURUSD_otc", hours=2, timeframe="1m")
        print("current candles:", len(current))
        print("history candles:", len(history))
        if current:
            print("sparkline:", sparkline([c.close for c in current]))
            last = current[-1]
            print("last close:", last.close, "| doji:", last.is_doji())


if __name__ == "__main__":
    main()
