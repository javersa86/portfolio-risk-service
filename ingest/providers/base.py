# Defines WHAT a price provider must do, not HOW. The fixture CSV, Tiingo,
# and Alpha Vantage each implement this, and the ingestion job only ever
# talks to this interface, so swapping sources never touches the job.
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)  # frozen = immutable once created
class PriceBar:
    """
    One ticker's prices for one trading day. Field names match the
    prices table's columns, so a bar maps straight onto a row.
    """

    ticker: str
    date: date
    open: Decimal | None      # Decimal, not float: exact, matches NUMERIC
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    adj_close: Decimal
    volume: int | None


class PriceProvider(Protocol):
    """
    Anything with this fetch() method counts as a provider.
    (Protocol = "duck typing" that mypy can check; no inheritance needed.)
    """

    def fetch(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        """Return daily bars for ticker between start and end, inclusive."""
        ...