# Fetches prices from the configured provider and upserts them into Postgres.
# Safe to run repeatedly: re-running updates rows instead of duplicating them.
import argparse
from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy import Engine, text

from api.app.config import settings
from api.app.db import get_engine
from ingest.providers import get_provider
from ingest.providers.base import PriceProvider

# Make sure the ticker exists first (prices.ticker has a foreign key to it).
# DO NOTHING: if it's already there, leave it alone.
UPSERT_TICKER = text(
    "INSERT INTO tickers (symbol) VALUES (:symbol) ON CONFLICT (symbol) DO NOTHING"
)

# The core of M1. ON CONFLICT (ticker, date) relies on the primary key:
#   - new (ticker, date)      -> INSERT a row
#   - existing (ticker, date) -> UPDATE it with the new values
# EXCLUDED is Postgres's name for "the row we just tried to insert".
UPSERT_PRICE = text(
    """
    INSERT INTO prices (ticker, date, open, high, low, close, adj_close, volume)
    VALUES (:ticker, :date, :open, :high, :low, :close, :adj_close, :volume)
    ON CONFLICT (ticker, date) DO UPDATE SET
        open        = EXCLUDED.open,
        high        = EXCLUDED.high,
        low         = EXCLUDED.low,
        close       = EXCLUDED.close,
        adj_close   = EXCLUDED.adj_close,
        volume      = EXCLUDED.volume,
        ingested_at = now()
    """
)


def ingest(
    engine: Engine, provider: PriceProvider, tickers: list[str], start: date, end: date
) -> int:
    """Upsert prices for each ticker. Returns the number of rows processed."""
    total = 0
    for ticker in tickers:
        # One transaction PER TICKER: if one ticker fails (bad data, API
        # error), the tickers already loaded stay committed.
        with engine.begin() as conn:  # begin() = commit on success, roll back on error
            conn.execute(UPSERT_TICKER, {"symbol": ticker})
            bars = provider.fetch(ticker, start, end)
            if bars:
                # Passing a LIST of dicts runs the statement once per row,
                # batched efficiently by the driver.
                conn.execute(UPSERT_PRICE, [asdict(bar) for bar in bars])
        total += len(bars)
        print(f"{ticker}: {len(bars)} rows")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest daily prices into Postgres.")
    parser.add_argument(
        "--start", type=date.fromisoformat, default=date.today() - timedelta(days=730)
    )  # ~2 years back
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--tickers", help="Comma-separated; overrides TICKERS in .env")
    args = parser.parse_args()

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")]
        if args.tickers
        else settings.ticker_list
    )
    total = ingest(
        get_engine(),
        get_provider(settings.price_provider, settings.price_provider_api_key),
        tickers,
        args.start,
        args.end,
    )
    print(f"Done: {total} rows upserted for {len(tickers)} tickers.")


if __name__ == "__main__":
    main()
