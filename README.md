# Fixed Income Risk Pipeline

An educational pipeline that generates synthetic fixed-income data, cleans inconsistent source files, calculates market risk, loads results into MySQL, and creates a daily Excel report.

All data is synthetic.

## Workflow

```text
Synthetic source feeds → Python ingestion → MySQL raw layer → SQL standardization
→ Python risk analytics → MySQL analytics layer → Excel report
```

The pipeline covers 90 business days, 5 portfolios, and 50 Treasuries, corporate bonds, interest-rate swaps, and MBS instruments.

## Project structure

```text
config/         Pipeline and risk parameters
data/raw/       Source-system CSV files
data/processed/ Clean data, ground truth, and analytics output
PROJECT_INTRO.md Architecture, table guide, conventions, and troubleshooting
docs/           Project introduction and daily Excel report
sql/            MySQL database, schema, and query scripts
src/            Data generation, SQL orchestration, analytics, validation, and reporting code
.env            Local MySQL credentials
requirements.txt Python dependencies
```

## Setup

```bash
cd "/Users/jzzz/Desktop/Project/fixed_income_risk_pipeline_project"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create `.env` in the project root:

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=fi_risk_user
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=fixed_income_risk_pipeline
```

Run `sql/00_init_database_and_user.sql` once as a MySQL administrator. The
pipeline then applies `01_schema.sql` and `02_transform_raw.sql` automatically.

## Run

Run the complete database-driven pipeline:

```bash
.venv/bin/python -m src.pipeline --full-history --load-db
```

Create a report for one date:

```bash
.venv/bin/python -m src.pipeline --date 2026-10-30 --load-db
```

Rerun analytics from existing standardized MySQL data without re-ingesting raw files:

```bash
.venv/bin/python -m src.run_analytics --date 2026-10-30 --persist --report
```

`src/etl.py` is retained only as an offline reference implementation and is not
called by either production command above. SQL is the authoritative
standardization path.

## Main MySQL tables

| Table | Content |
|---|---|
| `instrument` | Security reference data |
| `portfolio` | Portfolio reference data |
| `position_daily` | Daily standardized positions |
| `transaction_daily` | Standardized trades |
| `market_data_daily` | Prices, yields, spreads, and quote status |
| `risk_metric_daily` | Duration, convexity, DV01, CS01, and key-rate DV01 |
| `pnl_nav_daily` | NAV, P&L, and attribution |
| `portfolio_risk_daily` | Portfolio exposure, VaR, and backtesting flags |
| `stress_result_daily` | Stress-test results |
| `dq_exception` | Data-quality exceptions |
| `pipeline_run` | Pipeline execution audit |

## Processing responsibilities

- Python ingests position, market, transaction, NAV, and actual P&L source feeds into auditable raw JSON tables.
- SQL maps identifiers, removes duplicates, rejects missing quotes, flags stale data, and creates standardized calculation inputs.
- Python calculates duration, convexity, DV01/CS01, P&L attribution, historical VaR backtesting, and stress scenarios.
- `src/var_model.py` implements classical historical simulation using 60 security-level market-shock scenarios on fixed prior-day exposures, then backtests VaR against current actual P&L; `src/stress.py` owns the named SVB stress scenario.
- Calculated results are upserted to MySQL before the daily Excel review report is produced.

## Output

```text
docs/fixed_income_risk_2026-10-30.xlsx
```

The report contains portfolio risk, DV01/CS01, P&L attribution, VaR monitoring,
stress results, pricing validation, and data-quality exceptions.

## Model scope

UST and corporate-bond risk uses discounted cash flows, Macaulay duration, modified duration, textbook convexity, and DV01. IRS risk uses fixed-leg minus floating-leg valuation with ±1bp effective-duration/convexity repricing under explicit receive-fixed and flat-curve assumptions. MBS uses ±1bp effective-duration repricing of its current synthetic cash flows; it does not include production prepayment or OAS modeling. Key-rate DV01 uses local linear curve bumps and repricing. CS01, MBS volatility, and stress calculations remain educational rather than production models.

