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
src/            Data generation, ETL, analytics, database, and reporting code
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

Generate files and the Excel report without loading MySQL (offline demo mode):

```bash
.venv/bin/python -m src.pipeline --full-history
```

Run the complete database-driven pipeline:

```bash
.venv/bin/python -m src.pipeline --full-history --load-db
```

Create a report for one date:

```bash
.venv/bin/python -m src.pipeline --date 2026-10-30
```

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
- Calculated results are upserted to MySQL before the daily Excel review report is produced.

## Output

```text
docs/fixed_income_risk_2026-10-30.xlsx
```

The report contains portfolio risk, DV01/CS01, P&L attribution, VaR monitoring, stress results, and data-quality exceptions.

## Model scope

Bond analytics use a simplified fixed-coupon, flat-YTM model. Swap, MBS, key-rate, CS01, P&L attribution, VaR, and stress calculations are educational approximations, not production pricing models.

