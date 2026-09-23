import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from ingest.providers.base import PriceBar

# Absolute path to the CSV, built from THIS file's location, so it works
# no matter which folder you run the job from.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "prices.csv"


def _decimal_or_none(value: str) -> Decimal | None:
    """Empty CSV cell -> None; otherwise an exact Decimal."""
    return Decimal(value) if value else None


class FixtureProvider:
    """Serves prices from a local CSV. Used for tests and for developing
    without an API key."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    def fetch(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        bars: list[PriceBar] = []
        with self.path.open(newline="") as f:
            for row in csv.DictReader(f):  # each row -> dict keyed by the header
                day = date.fromisoformat(row["date"])
                if row["ticker"] != ticker or not (start <= day <= end):
                    continue
                bars.append(
                    PriceBar(
                        ticker=row["ticker"],
                        date=day,
                        open=_decimal_or_none(row["open"]),
                        high=_decimal_or_none(row["high"]),
                        low=_decimal_or_none(row["low"]),
                        close=Decimal(row["close"]),
                        adj_close=Decimal(row["adj_close"]),
                        volume=int(row["volume"]) if row["volume"] else None,
                    )
                )
        return sorted(bars, key=lambda b: b.date)