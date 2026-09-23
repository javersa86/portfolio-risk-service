from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from ingest.job import ingest
from ingest.providers.fixture import DEFAULT_PATH, FixtureProvider
from ingest.providers.tiingo import BASE_URL, TiingoProvider

START, END = date(2026, 9, 1), date(2026, 9, 30)


def count_prices(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM prices")).scalar_one())


# --- Provider tests (no database needed) ---------------------------------


def test_fixture_provider_filters_by_ticker_and_date() -> None:
    bars = FixtureProvider().fetch("AAPL", date(2026, 9, 15), date(2026, 9, 17))
    assert [b.date for b in bars] == [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    assert all(b.ticker == "AAPL" for b in bars)
    assert bars[0].close == Decimal("102.00")  # exact Decimal, not a float


def test_tiingo_maps_json_to_price_bars() -> None:
    # MockTransport = a fake network. The provider's code runs for real,
    # but no request ever leaves your machine, and no API key is needed.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["startDate"] == "2026-09-14"
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2026-09-14T00:00:00.000Z",
                    "open": 99.5,
                    "high": 100.5,
                    "low": 99.0,
                    "close": 100.0,
                    "adjClose": 99.8,
                    "volume": 50000000,
                }
            ],
        )

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    bars = TiingoProvider("test-key", client=client).fetch(
        "AAPL", date(2026, 9, 14), date(2026, 9, 14)
    )
    assert len(bars) == 1
    assert bars[0].date == date(2026, 9, 14)
    assert bars[0].adj_close == Decimal("99.8")


def test_tiingo_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Ticker not found"})

    client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        TiingoProvider("test-key", client=client).fetch("NOPE", START, END)


# --- Ingestion tests (real Postgres) --------------------------------------


def test_ingest_loads_all_rows(db: Engine) -> None:
    assert ingest(db, FixtureProvider(), ["AAPL", "SPY"], START, END) == 10
    assert count_prices(db) == 10


def test_ingest_is_idempotent(db: Engine) -> None:
    """The core M1 guarantee: running twice creates no duplicates."""
    ingest(db, FixtureProvider(), ["AAPL", "SPY"], START, END)
    ingest(db, FixtureProvider(), ["AAPL", "SPY"], START, END)
    assert count_prices(db) == 10


def test_ingest_updates_changed_price(db: Engine, tmp_path: Path) -> None:
    """A corrected price replaces the old row instead of adding a new one.
    tmp_path = a throwaway folder pytest creates, so the real CSV is untouched."""
    ingest(db, FixtureProvider(), ["AAPL"], START, END)

    edited = tmp_path / "prices.csv"
    edited.write_text(
        DEFAULT_PATH.read_text().replace(
            "AAPL,2026-09-18,101.00,104.50,100.80,104.00,104.00",
            "AAPL,2026-09-18,101.00,104.50,100.80,105.00,105.00",
        )
    )
    ingest(db, FixtureProvider(edited), ["AAPL"], START, END)

    with db.connect() as conn:
        close = conn.execute(
            text("SELECT close FROM prices WHERE ticker = 'AAPL' AND date = '2026-09-18'")
        ).scalar_one()
    assert close == Decimal("105.00")
    assert count_prices(db) == 5


def test_database_rejects_non_positive_price(db: Engine) -> None:
    """The CHECK constraint guards data no matter which code writes it."""
    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(text("INSERT INTO tickers (symbol) VALUES ('AAPL')"))
        conn.execute(
            text(
                "INSERT INTO prices (ticker, date, close, adj_close) "
                "VALUES ('AAPL', '2026-09-14', -1, -1)"
            )
        )
