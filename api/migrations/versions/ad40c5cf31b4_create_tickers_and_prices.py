"""create tickers and prices

Revision ID: ad40c5cf31b4
Revises:
Create Date: 2026-09-23 15:26:08.101477

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad40c5cf31b4"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Master list of symbols we track.
    op.execute(
        """
        CREATE TABLE tickers (
            symbol      TEXT PRIMARY KEY          -- 'AAPL'; unique by definition
                        CHECK (symbol = upper(symbol)),  -- forbid 'aapl' vs 'AAPL' duplicates
            name        TEXT,                     -- 'Apple Inc.' (optional)
            active      BOOLEAN NOT NULL DEFAULT TRUE,   -- stop ingesting without deleting history
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # One row per ticker per trading day.
    op.execute(
        """
        CREATE TABLE prices (
            ticker      TEXT NOT NULL
                        REFERENCES tickers (symbol)   -- must be a known ticker
                        ON DELETE CASCADE,            -- delete a ticker -> its prices go too
            date        DATE NOT NULL,                -- trading day (no time needed: end of day)
            open        NUMERIC(18, 6),               -- NUMERIC = exact decimals, not floats
            high        NUMERIC(18, 6),
            low         NUMERIC(18, 6),
            close       NUMERIC(18, 6) NOT NULL CHECK (close > 0),
            adj_close   NUMERIC(18, 6) NOT NULL CHECK (adj_close > 0),
                                                      -- adjusted for splits/dividends;
                                                      -- returns are computed from this
            volume      BIGINT CHECK (volume >= 0),   -- shares traded; can exceed 2 billion
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when WE loaded the row

            -- The key design decision of M1: a ticker can have only ONE row per date.
            -- 1) Makes ingestion idempotent: INSERT ... ON CONFLICT (ticker, date) DO UPDATE
            -- 2) Doubles as the index for "this ticker's prices, ordered by date",
            --    which is exactly what the window functions in M2 scan.
            PRIMARY KEY (ticker, date)
        )
        """
    )


def downgrade() -> None:
    # Undo in reverse order: prices references tickers, so drop it first.
    op.execute("DROP TABLE prices")
    op.execute("DROP TABLE tickers")
