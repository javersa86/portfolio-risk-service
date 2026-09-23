# Fetches daily prices from Tiingo's REST API.
# Docs: https://www.tiingo.com/documentation/end-of-day
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from ingest.providers.base import PriceBar

BASE_URL = "https://api.tiingo.com/tiingo/daily"


def _dec(value: Any) -> Decimal | None:
    """JSON number -> exact Decimal. str() first so ints and Decimals
    both convert cleanly; None stays None."""
    return None if value is None else Decimal(str(value))


class TiingoProvider:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        # Fail fast with a clear message instead of a confusing 401 later.
        if not api_key:
            raise ValueError("Tiingo needs PRICE_PROVIDER_API_KEY set in .env")
        # One reusable client: keeps the connection open across tickers.
        # The token goes in a HEADER, not the URL, so it never ends up
        # in logs or error messages that print URLs.
        # (client param lets tests inject a fake client: no real network.)
        self.client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,  # seconds; never let a hung request freeze the job
        )

    def fetch(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        response = self.client.get(
            f"/{ticker}/prices",
            params={"startDate": start.isoformat(), "endDate": end.isoformat()},
        )
        # 4xx/5xx (bad key, unknown ticker, rate limit) -> raise an error.
        # The job's per-ticker transaction means other tickers are unaffected.
        response.raise_for_status()

        # parse_float=Decimal: read JSON decimals straight into exact Decimals,
        # skipping float entirely (no 0.1 + 0.2 != 0.3 surprises).
        rows: list[dict[str, Any]] = response.json(parse_float=Decimal)
        return [self._to_bar(ticker, row) for row in rows]

    @staticmethod
    def _to_bar(ticker: str, row: dict[str, Any]) -> PriceBar:
        """Map Tiingo's JSON field names onto our PriceBar."""
        close = _dec(row["close"])
        adj_close = _dec(row["adjClose"])
        if close is None or adj_close is None:
            raise ValueError(f"{ticker} {row.get('date')}: missing close/adjClose")
        return PriceBar(
            ticker=ticker,
            # "2026-09-14T00:00:00.000Z" -> keep only the date part
            date=date.fromisoformat(row["date"][:10]),
            open=_dec(row.get("open")),
            high=_dec(row.get("high")),
            low=_dec(row.get("low")),
            close=close,
            adj_close=adj_close,
            volume=int(row["volume"]) if row.get("volume") is not None else None,
        )