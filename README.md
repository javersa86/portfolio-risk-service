# Portfolio Risk Service

A Python backend that ingests daily market prices, stores them in PostgreSQL, and computes
standard portfolio risk metrics (Value at Risk, volatility, drawdown, beta, correlation)
through a REST API, with a small React/TypeScript dashboard on top.

The risk analytics are implemented in **SQL** (PostgreSQL window functions, CTEs, and
ordered-set aggregates) and validated against an independent **pandas** reference
implementation, so every metric is computed two ways and cross-checked in CI.

The **TypeScript** dashboard's API types are generated from the backend's OpenAPI schema,
so the Python and TypeScript sides share one contract. A change to a response model in
Python breaks the frontend build until the dashboard is updated to match.

> ⚠️ **Status: Milestone 1 (foundation) is complete**: database, schema, idempotent price
> ingestion from a real market-data API, tests, and CI. The risk analytics, API, and
> dashboard described below are planned. See [How It Works Today](#how-it-works-today) for
> what runs now, and the [Roadmap](#roadmap) for what's next.

---

## Table of Contents
1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [How It Works Today](#how-it-works-today)
5. [Finance Terms in Plain English](#finance-terms-in-plain-english)
6. [Risk Metrics](#risk-metrics)
7. [SQL Design](#sql-design)
8. [API](#api)
9. [Type Safety Across the Stack](#type-safety-across-the-stack)
10. [Tech Stack](#tech-stack)
11. [Project Structure](#project-structure)
12. [Getting Started](#getting-started)
13. [Testing](#testing)
14. [Design Decisions](#design-decisions)
15. [Roadmap](#roadmap)
16. [Out of Scope](#out-of-scope)

---

## Overview

Portfolio Risk Service answers one question: *given a portfolio of tickers and weights,
how risky is it, based on recent market history?*

It runs as three cooperating pieces:

- **Ingestion job:** pulls end-of-day prices on a schedule and upserts them into Postgres.
  Re-running it is safe; it never creates duplicate rows.
- **API service:** a FastAPI app that stores portfolios and returns risk analytics over a
  configurable lookback window.
- **Dashboard:** a thin React + TypeScript frontend that visualizes the API's output.

---

## Features

- Scheduled, idempotent end-of-day price ingestion behind a swappable data-provider adapter
- Portfolio CRUD (tickers + weights, validated to sum to 1.0)
- Risk analytics over a configurable lookback window (default 252 trading days, about one year):
  - Daily and annualized volatility
  - 1-day Value at Risk at 95% and 99%, both historical and parametric
  - Maximum drawdown
  - Beta against a benchmark (SPY by default)
  - Pairwise correlation matrix
- Analytics computed in PostgreSQL, cross-checked against pandas in the test suite
- Auto-generated OpenAPI docs at `/docs`
- React + TypeScript (strict mode) dashboard with API types generated from the OpenAPI schema
- Fully containerized with Docker Compose; CI on GitHub Actions

---

## Architecture

```mermaid
flowchart LR
    P[Market data provider] -->|daily EOD prices| I[Ingestion job<br/>scheduled]
    I -->|upsert| DB[(PostgreSQL<br/>prices, portfolios,<br/>materialized returns)]
    API[FastAPI service] -->|analytics SQL| DB
    API -->|CRUD| DB
    UI[React + TS dashboard] -->|REST / JSON| API
```

- The **provider adapter** isolates the data source. Swapping providers, or using fixture
  data in tests, means implementing one interface rather than touching ingestion logic.
- **Daily returns** are precomputed in a materialized view, refreshed after each ingest, so
  analytics queries read returns directly instead of recomputing them per request.
- The **API** does no heavy math in Python for the main request path. It issues SQL and
  shapes the results. pandas is used as the independent reference in tests.
- The **dashboard** talks to the API only through a typed client generated from the
  OpenAPI schema. In development, Vite proxies `/api` to FastAPI; in production, FastAPI
  serves the built dashboard as static files, so the whole app runs at a single URL.

---

## How It Works Today

What is built and running as of Milestone 1. Everything here is covered by the test suite
and runs in CI on every push.

```mermaid
flowchart LR
    ENV[.env] -->|settings| JOB[ingest.job]
    CSV[Fixture CSV] -.->|PRICE_PROVIDER=fixture| JOB
    TI[Tiingo REST API] -.->|PRICE_PROVIDER=tiingo| JOB
    JOB -->|upsert, one transaction per ticker| DB[(PostgreSQL 16<br/>tickers, prices)]
    ALE[Alembic migration] -->|creates schema| DB
```

### 1. Configuration
All settings live in `.env` (copied from `.env.example`, never committed). `api/app/config.py`
loads them with pydantic-settings, so no connection string or API key is hard-coded
anywhere. See [Configuration](#configuration) for every variable.

### 2. Database and schema
PostgreSQL 16 runs in Docker Compose, with its data in a named volume so it survives
restarts. The schema is created by an Alembic migration written in plain SQL
(`api/migrations/versions/`):

- **`tickers`**: one row per symbol. A `CHECK` forces uppercase, so `aapl` and `AAPL` can't
  both exist.
- **`prices`**: one row per ticker per trading day, with the composite primary key
  `(ticker, date)`. Prices are `NUMERIC(18, 6)`, exact decimals rather than floats, and
  `CHECK` constraints reject non-positive prices and negative volume at the database level.
  A foreign key ties every price to a known ticker.

The composite key is the central design choice: it is what makes ingestion idempotent, and
it doubles as the index the Milestone 2 window functions will scan.

### 3. Price providers (the adapter pattern)
The ingestion job never talks to a data source directly. It depends on one small interface,
`PriceProvider.fetch(ticker, start, end) -> list[PriceBar]` (`ingest/providers/base.py`),
and `get_provider()` picks the implementation from `PRICE_PROVIDER`:

| Provider | Source | Used for |
|---|---|---|
| `fixture` | `ingest/fixtures/prices.csv`: 5 days of made-up AAPL and SPY prices with round numbers | Tests, and development without an API key |
| `tiingo` | Tiingo's end-of-day REST API | Real data |

The Tiingo provider sends the API token in a request header (never the URL, so it can't
leak into logs), sets a 30-second timeout, raises on any HTTP error, and parses JSON numbers
straight into `Decimal` so prices never pass through floating point.

### 4. Ingestion job
`python -m ingest.job` does the following for each ticker:

1. Inserts the ticker into `tickers` if it isn't there yet (`ON CONFLICT DO NOTHING`).
2. Fetches daily bars from the configured provider.
3. Upserts them into `prices` with `INSERT ... ON CONFLICT (ticker, date) DO UPDATE`: new
   days are inserted, existing days are overwritten with the latest values, and
   `ingested_at` records when each row was last loaded.

Each ticker runs in **its own transaction**, so one failing ticker (a bad symbol, an API
error) rolls back only itself and the tickers already loaded stay committed. **Running the
job twice never creates duplicates**, and a corrected price from the provider replaces the
old value in place.

Current dataset: **8 tickers × 501 trading days** (2024-09-23 to 2026-09-22).

### 5. Tests
`conftest.py` gives the tests a real Postgres database, `risk_test`, separate from
development data. It creates the database if needed, runs the real Alembic migrations on
it, and empties the tables before each test. The seven tests cover:

| Test | Proves |
|---|---|
| `test_fixture_provider_filters_by_ticker_and_date` | The CSV provider returns only the requested ticker and dates, in order, as exact `Decimal`s |
| `test_tiingo_maps_json_to_price_bars` | Tiingo's JSON maps correctly onto `PriceBar`, using a mocked HTTP transport with no network and no API key |
| `test_tiingo_raises_on_http_error` | An HTTP error (e.g. unknown ticker) raises instead of silently returning nothing |
| `test_ingest_loads_all_rows` | A fresh ingest loads every row |
| `test_ingest_is_idempotent` | Running ingestion twice leaves the row count unchanged |
| `test_ingest_updates_changed_price` | A changed price updates the existing row rather than adding one |
| `test_database_rejects_non_positive_price` | The `CHECK` constraint blocks bad data regardless of which code writes it |

### 6. Continuous integration
`.github/workflows/ci.yml` runs on every push and pull request: a fresh Ubuntu runner,
a throwaway Postgres 16 service container, Python 3.11, then four gates in order:
`ruff format --check`, `ruff check`, `mypy` in strict mode, and `pytest`. Any failure marks
the commit red. CI needs no secrets, since no test touches the real API.

---

## Finance Terms in Plain English

No finance background needed. Every term the project uses, explained with one running
example: **you have $100, and you put $60 into Apple and $40 into Microsoft.**

### The setup

| Term | Plain English |
|---|---|
| **Stock** | A tiny piece of a company. Owning one Apple share means owning a very small slice of Apple. |
| **Ticker** | A stock's short nickname: Apple is `AAPL`, Microsoft is `MSFT`. |
| **End-of-day price** | Prices change all day, so the project records one price per day, at market close, like marking a height chart once a night. This is what the ingestion job collects. |
| **Portfolio** | Your basket of stocks. Here: Apple and Microsoft. |
| **Weights** | How the basket is split. $60 of $100 in Apple is a weight of 0.6; Microsoft is 0.4. Weights always add up to 1. |
| **Benchmark / SPY** | Something to compare your basket against. SPY is a single ticker that holds the 500 biggest US companies, so it effectively stands for "the market." |
| **Lookback window (252)** | How far back the math looks. The stock market is open about 252 days a year, so a window of 252 means "the last year." |

### The measurements

Each metric answers one question.

| Metric | The question | Plain English |
|---|---|---|
| **Return** | *Did I win or lose today?* | Apple was $100 yesterday and $102 today: a 2% gain, a return of +0.02. A drop to $99 is −0.01. The basket's return mixes each stock's return by its weight: 60% of Apple's move plus 40% of Microsoft's. |
| **Volatility** | *How bumpy is the ride?* | Two roller coasters can end at the same height, but one is a gentle kiddie ride and the other lurches up and down. Volatility measures the lurching: how much daily returns jump around. "Annualized" rescales the daily number into a yearly one. |
| **VaR (Value at Risk)** | *On a bad day, how much could I lose?* | Line up a year of days from worst to best and look at the worst 5%, about 13 days. The best of those bad days is the **95% VaR**: *"on 95 days out of 100, I won't lose more than this."* A VaR of 2% on a $100 basket means losses beyond $2 happen only about 1 day in 20. **99% VaR** is the same idea for the rarer, worse days (1 in 100). |
| **Historical VaR** | | Counts what actually happened on real past days. No assumptions. |
| **Parametric VaR** | | Assumes days follow a neat bell curve and calculates instead of counting. Faster and smoother, but real markets have more disaster days than a bell curve predicts, which is why the project reports both. |
| **Max drawdown** | *What's the worst fall I lived through?* | The basket climbs to $130, then slides to $100 before recovering: a 23% fall from peak to bottom. Max drawdown is the biggest such fall in the window. VaR is about one bad day; drawdown is about a whole bad stretch. |
| **Beta** | *Does my basket just follow the crowd?* | How hard the basket moves when the market (SPY) moves. **Beta 1:** market up 1%, basket up about 1%. **Beta 2:** twice as much, both ways. **Beta 0.5:** half as much, a calmer basket. |
| **Correlation** | *Do my stocks move together?* | A score from −1 to +1 for each pair of stocks. **+1:** always move the same way, like twins holding hands. **0:** no relationship. **−1:** opposites, like a seesaw. If everything in a basket is highly correlated, it all falls on the same day, which is why people diversify. The correlation matrix is a grid of these scores for every pair. |

### The whole project in one sentence

*Every night, write down what each stock cost. Then, for any basket, answer: how bumpy is
it, how much could it lose on a bad day, what was its worst fall, how much does it follow
the market, and do its stocks all move together?*

The next section gives the exact formulas.

---

## Risk Metrics

| Metric | Definition | Notes |
|---|---|---|
| Daily return | `r_t = P_t / P_{t-1} - 1` | Simple returns, per ticker |
| Portfolio return | `R_t = Σ w_i · r_{i,t}` | Fixed weights, rebalanced daily |
| Volatility | `σ = stddev(R)` over the window; annualized as `σ · √252` | Sample standard deviation |
| Historical VaR (α) | Negative of the `(1 − α)` percentile of `R` over the window | No distributional assumption |
| Parametric VaR (α) | `−(μ − z_α · σ)` | Assumes normal returns; shown alongside historical VaR for comparison |
| Max drawdown | Largest peak-to-trough decline in cumulative portfolio value | Reported with peak and trough dates |
| Beta | `cov(R, R_bench) / var(R_bench)` | Benchmark configurable, SPY by default |
| Correlation | Pearson correlation of daily returns, pairwise | Returned as a matrix |

VaR is reported as a positive loss fraction of portfolio value (e.g. `0.021` means a 2.1%
one-day loss is expected to be exceeded on only 5% of days at 95% confidence).

---

## SQL Design

### Schema

| Table / View | Purpose | Status |
|---|---|---|
| `tickers` | Symbol, name, active flag | ✅ Built (M1) |
| `prices` | `(ticker, date)` primary key, OHLC + adjusted close, volume | ✅ Built (M1) |
| `portfolios` | Portfolio metadata | Planned (M2) |
| `portfolio_weights` | `(portfolio_id, ticker)` primary key, weight | Planned (M2) |
| `daily_returns` | Materialized view of per-ticker daily returns, refreshed after ingest | Planned (M2) |

### Key techniques

| Need | SQL feature |
|---|---|
| Idempotent ingestion ✅ | `INSERT ... ON CONFLICT (ticker, date) DO UPDATE` |
| Daily returns | `LAG(adj_close) OVER (PARTITION BY ticker ORDER BY date)` |
| Rolling volatility | `STDDEV_SAMP(ret) OVER (PARTITION BY ticker ORDER BY date ROWS 251 PRECEDING)` |
| Portfolio returns | Join `daily_returns` to `portfolio_weights`, `SUM(ret * weight)` grouped by date |
| Historical VaR | `PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY portfolio_ret)` |
| Max drawdown | CTEs: cumulative value, running `MAX() OVER (... ROWS UNBOUNDED PRECEDING)`, worst drop from peak |
| Beta / correlation | `COVAR_SAMP`, `VAR_SAMP`, `CORR` aggregates, self-join for pairwise correlation |
| Performance | Composite index on `(ticker, date)`; `EXPLAIN ANALYZE` before/after results documented here |

Query plans and timings will be recorded in this section once the analytics are built.

---

## API

Planned endpoints. Full interactive docs are served at `/docs` when the service is running.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/tickers` | List tracked tickers |
| `GET` | `/prices/{ticker}?start=&end=` | Price history for one ticker |
| `POST` | `/portfolios` | Create a portfolio (`name`, `weights: {ticker: weight}`) |
| `GET` | `/portfolios/{id}` | Portfolio details |
| `DELETE` | `/portfolios/{id}` | Delete a portfolio |
| `GET` | `/portfolios/{id}/risk?window=252` | Full risk summary: volatility, VaR, drawdown, beta |
| `GET` | `/portfolios/{id}/correlation?window=252` | Correlation matrix for the portfolio's tickers |

Example response for `/portfolios/{id}/risk` (shape only, values illustrative):

```json
{
  "portfolio_id": 1,
  "window": 252,
  "as_of": "2026-09-22",
  "volatility": { "daily": 0.0112, "annualized": 0.1778 },
  "var": {
    "historical": { "95": 0.0181, "99": 0.0297 },
    "parametric": { "95": 0.0179, "99": 0.0254 }
  },
  "max_drawdown": { "value": 0.142, "peak": "2026-02-18", "trough": "2026-04-08" },
  "beta": { "benchmark": "SPY", "value": 1.08 }
}
```

---

## Type Safety Across the Stack

Types are enforced on both sides of the network boundary, and the two sides are linked:

```
Pydantic models (Python)  ──►  OpenAPI schema (/openapi.json)  ──►  openapi-typescript  ──►  web/src/api/schema.ts
```

1. **Python:** request and response shapes are Pydantic models, checked by mypy.
2. **Contract:** FastAPI publishes those models as an OpenAPI schema automatically.
3. **TypeScript:** `openapi-typescript` generates `schema.ts` from that schema, and the
   dashboard's fetch client (`openapi-fetch`) is typed against it, so paths, query
   parameters, and response fields are all checked at compile time.
4. **CI enforcement:** CI regenerates the types and fails if the committed `schema.ts` is
   out of date, then runs `tsc --noEmit` in strict mode. A backend change that breaks the
   frontend cannot merge silently.

Example: the dashboard's risk call, fully typed with no hand-written interfaces:

```ts
const { data, error } = await api.GET("/portfolios/{id}/risk", {
  params: { path: { id }, query: { window: 252 } },
});
// data.var.historical["95"] is typed; a typo or a renamed field is a compile error
```

---

## Tech Stack

### Backend
| Tool | Role |
|---|---|
| Python 3.12 | Language |
| FastAPI + Pydantic | REST API, request/response validation, OpenAPI docs |
| PostgreSQL 16 | Storage and analytics |
| SQLAlchemy 2.0 | CRUD via the ORM; analytics via hand-written SQL through SQLAlchemy Core |
| Alembic | Schema migrations |
| pandas / NumPy | Reference implementation of every metric, used in tests |
| APScheduler | Scheduled ingestion |

### Frontend
| Tool | Role |
|---|---|
| React + TypeScript + Vite | Dashboard, TypeScript in `strict` mode |
| openapi-typescript + openapi-fetch | Types and a typed API client generated from the backend's OpenAPI schema |
| TanStack Query | Data fetching, caching, loading and error states |
| Recharts | Drawdown chart, correlation heatmap |
| Vitest + React Testing Library | Component tests |
| oxlint | Lint (same linter as windows-cube-game-console) |

### Quality and delivery
| Tool | Role |
|---|---|
| pytest | Unit and integration tests, run against a real Postgres container |
| ruff, mypy | Lint and static types |
| Docker Compose | Local environment (db, api, ingest, web) |
| GitHub Actions | CI on every push: Python (ruff, mypy, pytest) and TypeScript (schema drift check, `tsc`, oxlint, Vitest) |

---

## Project Structure

Planned layout:

```
portfolio-risk-service/
├── api/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory, router registration
│   │   ├── routers/             # health, tickers, prices, portfolios, risk
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── models/              # SQLAlchemy ORM models (CRUD tables)
│   │   ├── analytics/
│   │   │   ├── sql/             # Hand-written analytics queries (.sql)
│   │   │   └── reference.py     # pandas reference implementations
│   │   └── db.py                # Engine and session setup
│   ├── migrations/              # Alembic
│   └── tests/
│       ├── fixtures/            # Deterministic price data
│       └── ...
├── ingest/
│   ├── providers/               # Data-provider adapter interface + implementations
│   ├── job.py                   # Fetch + upsert (materialized-view refresh added in M2)
│   └── tests/
├── web/                         # React + TS dashboard
│   ├── src/
│   │   ├── api/
│   │   │   ├── schema.ts        # Generated from /openapi.json, do not edit by hand
│   │   │   └── client.ts        # Typed openapi-fetch client
│   │   ├── components/          # VarCard, DrawdownChart, CorrelationHeatmap, ...
│   │   ├── hooks/               # TanStack Query hooks (useRisk, usePortfolios, ...)
│   │   └── pages/
│   ├── vite.config.ts           # Dev proxy: /api → FastAPI
│   └── tsconfig.json            # strict: true
├── docker-compose.yml
├── .github/workflows/ci.yml
└── README.md
```

---

## Getting Started

Commands are shown for Windows PowerShell; on macOS/Linux, activate the virtual environment
with `source .venv/bin/activate` instead.

### Prerequisites
- Docker Desktop (running)
- Python 3.11+ (CI runs on 3.11)
- A free [Tiingo](https://www.tiingo.com/) API token, only for real market data. Everything
  else, including the full test suite, works without one.

### 1. Install
```powershell
git clone https://github.com/javersa86/portfolio-risk-service.git
cd portfolio-risk-service
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

### 2. Start Postgres and create the schema
```powershell
docker compose up -d db          # Postgres 16 in the background
docker compose ps                # wait until db shows "healthy"
alembic upgrade head             # create the tickers and prices tables
```

### 3. Load prices
With the default `PRICE_PROVIDER=fixture`, no API key needed:
```powershell
python -m ingest.job --start 2026-09-01 --end 2026-09-30 --tickers AAPL,SPY
```
For real data, set `PRICE_PROVIDER=tiingo` and `PRICE_PROVIDER_API_KEY` in `.env`, then
backfill two years for every symbol in `TICKERS`:
```powershell
python -m ingest.job
```

| Option | Default | Meaning |
|---|---|---|
| `--start` | today minus 730 days | First date to fetch (`YYYY-MM-DD`) |
| `--end` | today | Last date to fetch, inclusive |
| `--tickers` | `TICKERS` from `.env` | Comma-separated symbols, overriding `.env` |

Re-running any of these is safe: existing rows update in place, and nothing duplicates.

### 4. Inspect the data
```powershell
docker compose exec db psql -U risk -d risk -c "SELECT ticker, count(*), min(date), max(date) FROM prices GROUP BY ticker ORDER BY ticker;"
```

### Useful commands
| Command | What it does |
|---|---|
| `docker compose logs db` | Postgres logs, the first place to look if something won't connect |
| `docker compose down` | Stop Postgres; data is kept in the volume |
| `docker compose down -v` | Stop Postgres **and delete all data**, for a clean start (rerun step 2 afterward) |
| `alembic downgrade base` | Drop all tables through the migrations |

### Coming in later milestones
The API (`uvicorn`, http://localhost:8000/docs), a one-command `docker compose up` for the
whole stack, and the dashboard (http://localhost:5173) arrive in Milestones 2 to 4.

### Dashboard development (Milestone 4, planned)
```bash
cd web
npm install
npm run gen:types   # regenerate src/api/schema.ts from the running API's /openapi.json
npm run dev         # Vite dev server with /api proxied to FastAPI
npm run typecheck   # tsc --noEmit
```
Run `gen:types` whenever a Pydantic request or response model changes.

### Configuration
Set in `.env`. `.env.example` documents each one.

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `risk` / `risk` / `risk` | Used by the Postgres container on first start (local development only) |
| `DATABASE_URL` | `postgresql+psycopg://risk:risk@localhost:5432/risk` | Connection used by ingestion and Alembic |
| `PRICE_PROVIDER` | `fixture` | `fixture` (local CSV) or `tiingo` (real data). Stooq was ruled out 2026-09-23 because it blocks scripted downloads |
| `PRICE_PROVIDER_API_KEY` | *(empty)* | Tiingo token. **Only in `.env`, never committed** |
| `TICKERS` | `SPY,QQQ,AAPL,MSFT,JPM,GS,TLT,GLD` | Symbols to ingest |
| `TEST_DATABASE_URL` | `postgresql+psycopg://risk:risk@localhost:5432/risk_test` | Test database; created automatically by the test suite |

Planned for Milestone 3: `INGEST_CRON` (default `0 22 * * 1-5`, after US market close) for
scheduled ingestion.

---

## Testing

With Postgres running (`docker compose up -d db`), run the same four checks CI runs:
```powershell
ruff format --check .    # formatting (drop --check to auto-fix)
ruff check .             # lint (add --fix for safe automatic fixes)
mypy api ingest          # strict type checking
pytest -v                # 7 tests against a real Postgres test database
```
The tests use their own `risk_test` database and never touch development data or the
network. See [How It Works Today](#5-tests) for what each test proves.

**In place now (M1):**
- **Deterministic fixtures:** tests run against a fixed price dataset, never live market data.
- **Real database:** integration tests run against Postgres, not SQLite, with the real
  migrations applied, so constraints and SQL behave exactly as in production.
- **Mocked HTTP:** the Tiingo provider is tested with `httpx.MockTransport`, with no network
  and no API key.

**Planned:**
- **SQL vs. pandas cross-check (M2):** each metric's SQL implementation is asserted to match its
  pandas reference within a tolerance. A disagreement fails CI.
- **Hand-verified cases (M2):** a handful of tiny datasets with metrics computed by hand,
  so both implementations are checked against known answers, not just each other.
- **Frontend (M4):** `npm test` runs Vitest component tests against mocked API responses built
  from the generated types, so the mocks can't drift from the real response shape.
- **Contract check (M4):** CI fails if `web/src/api/schema.ts` doesn't match the backend's
  current OpenAPI schema.

---

## Design Decisions

To be filled in as the project is built. Planned topics:

- **Analytics in SQL vs. in pandas:** less data moved to the app, set-based computation that
  scales with the database, and where pandas is still the better tool.
- **Historical vs. parametric VaR:** why both are shown, and where the normal-distribution
  assumption breaks down (fat tails).
- **Materialized returns view:** read performance vs. refresh cost, and when to refresh.
- **Provider adapter:** isolating a free, occasionally unreliable data source from the rest
  of the system.
- **Generated types vs. hand-written interfaces:** one source of truth for the API
  contract, and why schema drift is checked in CI.
- **Single-URL deployment:** FastAPI serving the built dashboard vs. separate frontend
  hosting with CORS.
- **Simple returns and daily rebalancing:** simplifying assumptions and their effect on the
  numbers.

---

## Roadmap

### Milestone 1: Foundation
- [X] Repo scaffold, Docker Compose (Postgres)
- [X] Schema and Alembic migrations
- [X] Provider adapter interface + first provider
- [X] Idempotent ingestion with upsert
- [X] Fixture dataset and first tests
- [X] CI green (ruff, mypy, pytest)

### Milestone 2: Analytics and API
- [ ] `daily_returns` materialized view
- [ ] SQL implementations: volatility, historical and parametric VaR, drawdown, beta, correlation
- [ ] pandas reference implementations and cross-check tests
- [ ] Portfolio CRUD and risk endpoints
- [ ] `EXPLAIN ANALYZE` results in [SQL Design](#sql-design)

### Milestone 3: Deploy
- [ ] API and ingestion job as Docker Compose services (one-command `docker compose up`)
- [ ] Deployed to a hosted environment with a live URL
- [ ] Scheduled ingestion running in production
- [ ] Architecture and design-decision sections completed

### Milestone 4: Dashboard
- [ ] Vite + React + TypeScript (strict) scaffold with the `/api` dev proxy
- [ ] Generated OpenAPI types + typed client; schema drift check in CI
- [ ] Portfolio picker, VaR card, drawdown chart, correlation heatmap
- [ ] Loading, empty, and error states via TanStack Query
- [ ] Vitest component tests; `tsc` and oxlint in CI
- [ ] Dashboard served by FastAPI at the same URL as the API

---

## Out of Scope

Deliberately excluded to keep the project small and finishable:

- User accounts and authentication
- Intraday or real-time streaming data
- Options, derivatives pricing, or counterparty credit risk (CVA)
- Transaction costs, rebalancing strategies, or backtesting

---

## Disclaimer

This is a portfolio and learning project. Its output is not investment advice and should
not be used for real trading or risk decisions.
