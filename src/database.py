import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, create_engine, text

from .config import mysql_url


TABLE_FILES = {
    "instrument": "ground_truth_instrument.csv",
    "portfolio": "ground_truth_portfolio.csv",
    "position_daily": "position_clean.csv",
    "transaction_daily": "transaction_clean.csv",
    "market_data_daily": "market_clean.csv",
    "risk_metric_daily": "risk_metric.csv",
    "pnl_nav_daily": "pnl_nav.csv",
    "portfolio_risk_daily": "portfolio_risk.csv",
    "stress_result_daily": "stress_results.csv",
}

RAW_FILES = (
    ("raw_position", "positions_source_a.csv", "POS_SYS", "AsOfDate"),
    ("raw_market_data", "market_source_b.csv", "MKT_SYS", "valuation_dt"),
    ("raw_transaction", "transactions_source_c.csv", "TRADE_SYS", "EXEC_TS"),
    ("raw_nav", "nav_source_d.csv", "NAV_SYS", "business_date"),
    ("raw_pnl", "pnl_source_e.csv", "PNL_SYS", "business_date"),
)


class DatabaseClient:
    """Manage the MySQL engine, run ID and database pipeline operations."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = None
        self.run_id = None
        self.ingested_rows = 0

    def ingest_and_standardize(self, business_date=None):
        """Load raw files, run SQL transformations and return standardized inputs."""
        self.engine, self.run_id, self.ingested_rows = ingest_and_standardize(
            self.cfg, business_date
        )
        return self.read_standardized()

    def read_standardized(self):
        """Return calculation-ready MySQL tables as pandas DataFrames."""
        if self.engine is None:
            self.engine = initialize(self.cfg["root"])
        return read_standardized(self.engine)

    def begin_analytics_run(self):
        """Create an audit run for analytics-only calculation and persistence."""
        if self.engine is None:
            self.engine = initialize(self.cfg["root"])
        self.run_id = _create_run(self.engine, None, mode="ANALYTICS")
        self.ingested_rows = 0
        return self.run_id

    def persist_analytics(self, frames):
        """Write the four analytics outputs and finish the database run audit."""
        if self.engine is None or self.run_id is None:
            raise RuntimeError("Run ingest_and_standardize() before persist_analytics().")
        return persist_analytics(
            self.engine, self.run_id, frames, self.ingested_rows
        )

    def read_run_exceptions(self):
        """Return data-quality exceptions for the active pipeline run."""
        if self.engine is None or self.run_id is None:
            raise RuntimeError("No active database pipeline run is available.")
        return read_run_exceptions(self.engine, self.run_id)


def _records(frame):
    """Return MySQL-safe dictionaries with pandas missing values as None."""
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def _read_csv(path, business_date=None):
    frame = pd.read_csv(path)
    if business_date and "business_date" in frame.columns:
        frame = frame[frame["business_date"].astype(str) == business_date]
    return frame


def initialize(root):
    database = os.getenv("MYSQL_DATABASE", "fixed_income_risk_pipeline")
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError("MYSQL_DATABASE may contain only letters, numbers, and underscores")

    admin_engine = create_engine(mysql_url(include_db=False))
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4"
        )

    engine = create_engine(mysql_url(include_db=True), pool_pre_ping=True)
    schema = (Path(root) / "sql/01_schema.sql").read_text(encoding="utf-8")
    schema = schema.replace("USE fixed_income_risk_pipeline;", "")
    with engine.begin() as connection:
        for statement in schema.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)
        existing = set(connection.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:database AND TABLE_NAME='pnl_nav_daily'"
        ), {"database": database}).scalars())
        migrations = {
            "duration_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER carry_pnl",
            "convexity_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER duration_pnl",
            "trading_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER spread_pnl",
        }
        for column, definition in migrations.items():
            if column not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE pnl_nav_daily ADD COLUMN {column} {definition}"
                )
        risk_existing = set(connection.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=:database AND TABLE_NAME='risk_metric_daily'"
        ), {"database": database}).scalars())
        risk_migrations = {
            "macaulay_duration": "DECIMAL(14,8) NULL AFTER market_value",
            "duration_type": "VARCHAR(20) NOT NULL DEFAULT 'MODIFIED' AFTER modified_duration",
        }
        for column, definition in risk_migrations.items():
            if column not in risk_existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE risk_metric_daily ADD COLUMN {column} {definition}"
                )
        table_migrations = {
            "portfolio_risk_daily": {
                "realized_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER cs01",
                "hypothetical_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER realized_pnl",
                "residual_pnl": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER hypothetical_pnl",
            },
            "stress_result_daily": {
                "nav": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER scenario_name",
                "stress_loss": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER stress_pnl",
                "stress_limit": "DECIMAL(22,4) NOT NULL DEFAULT 0 AFTER stress_loss",
                "stress_limit_breach": "BOOLEAN NOT NULL DEFAULT FALSE AFTER stress_limit",
            },
        }
        for table, columns in table_migrations.items():
            existing_columns = set(connection.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=:database AND TABLE_NAME=:table"
            ), {"database": database, "table": table}).scalars())
            for column, definition in columns.items():
                if column not in existing_columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )
    return engine


def upsert_frame(connection, table, frame):
    if frame.empty:
        return 0

    columns = list(frame.columns)
    placeholders = ",".join(f":{column}" for column in columns)
    updates = ",".join(f"{column}=VALUES({column})" for column in columns)
    statement = text(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    connection.execute(statement, _records(frame))
    return len(frame)


def _create_run(engine, business_date, mode=None):
    """Create one auditable ingestion or analytics execution record."""
    run_id = str(uuid.uuid4())
    params = {
        "run_id": run_id,
        "business_date": business_date,
        "mode": mode or ("DAILY" if business_date else "FULL"),
        "started_at": datetime.now(),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO pipeline_run "
                "(run_id, business_date, mode, started_at, status) "
                "VALUES (:run_id, :business_date, :mode, :started_at, 'RUNNING')"
            ),
            params,
        )
    return run_id


def _finish_run(engine, run_id, status, rows_loaded=0, error=None):
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE pipeline_run SET finished_at=:finished_at, status=:status, "
                "rows_loaded=:rows_loaded, error_message=:error WHERE run_id=:run_id"
            ),
            {
                "finished_at": datetime.now(),
                "status": status,
                "rows_loaded": rows_loaded,
                "error": error,
                "run_id": run_id,
            },
        )


def _load_reference_data(connection, processed):
    instruments = pd.read_csv(processed / "ground_truth_instrument.csv")
    portfolios = pd.read_csv(processed / "ground_truth_portfolio.csv")
    upsert_frame(connection, "instrument", instruments)
    upsert_frame(connection, "portfolio", portfolios)

    aliases = []
    for row in instruments.itertuples(index=False):
        for value in (row.instrument_id, row.cusip, row.isin):
            aliases.append({"source_system": "ALL", "source_identifier": str(value),
                            "instrument_id": row.instrument_id})
    upsert_frame(connection, "identifier_alias", pd.DataFrame(aliases))

    portfolio_aliases = []
    for row in portfolios.itertuples(index=False):
        for value in (row.portfolio_id, row.portfolio_name,
                      row.portfolio_name.lower().replace(" ", "_")):
            portfolio_aliases.append({"source_system": "ALL", "source_name": value,
                                      "portfolio_id": row.portfolio_id})
    upsert_frame(connection, "portfolio_alias", pd.DataFrame(portfolio_aliases))


def _insert_raw_file(connection, table, path, source, run_id, date_column, business_date=None):
    frame = pd.read_csv(path)
    if business_date:
        values = pd.to_datetime(frame[date_column], errors="coerce").dt.date.astype(str)
        frame = frame[values == business_date]
    now = datetime.now()
    rows = [{"run_id": run_id, "source": source,
             "payload": json.dumps(row, default=str, allow_nan=False), "ingested_at": now}
            for row in _records(frame)]
    if rows:
        connection.execute(text(
            f"INSERT INTO {table} (run_id, source_system, payload, ingested_at) "
            "VALUES (:run_id, :source, :payload, :ingested_at)"), rows)
    return len(rows)


def ingest_and_standardize(cfg, business_date=None):
    """Ingest source files unchanged, then let MySQL standardize and validate them."""
    root = Path(cfg["root"])
    raw = root / cfg["paths"]["raw"]
    processed = root / cfg["paths"]["processed"]
    engine = initialize(root)
    run_id = _create_run(engine, business_date)
    ingested = 0
    try:
        with engine.begin() as connection:
            _load_reference_data(connection, processed)
            for table, filename, source, date_column in RAW_FILES:
                ingested += _insert_raw_file(connection, table, raw / filename, source,
                                             run_id, date_column, business_date)
            script = (root / "sql/02_transform_raw.sql").read_text(encoding="utf-8")
            params = {"run_id": run_id, "created_at": datetime.now(),
                      "stale_days": cfg["risk"]["stale_business_days"]}
            for statement in script.split(";"):
                if statement.strip():
                    connection.execute(text(statement), params)
        return engine, run_id, ingested
    except Exception as exc:
        _finish_run(engine, run_id, "FAILED", error=str(exc)[:4000])
        raise


def read_standardized(engine):
    """Read only standardized calculation inputs, not the raw landing tables."""
    names = ("position_daily", "market_data_daily", "transaction_daily",
             "nav_daily", "actual_pnl_daily")
    with engine.connect() as connection:
        return {name: pd.read_sql(text(f"SELECT * FROM {name}"), connection)
                for name in names}


def persist_analytics(engine, run_id, frames, ingested_rows=0):
    tables = ("risk_metric_daily", "pnl_nav_daily", "portfolio_risk_daily",
              "stress_result_daily")
    loaded = 0
    try:
        with engine.begin() as connection:
            for table, frame in zip(tables, frames):
                if table in ("risk_metric_daily", "stress_result_daily") and not frame.empty:
                    dates = sorted({str(value)[:10] for value in frame["business_date"]})
                    connection.execute(text(f"DELETE FROM {table} WHERE business_date IN :dates")
                                       .bindparams(bindparam("dates", expanding=True)),
                                       {"dates": dates})
                loaded += upsert_frame(connection, table, frame)
        _finish_run(engine, run_id, "SUCCESS", rows_loaded=ingested_rows + loaded)
        return loaded
    except Exception as exc:
        _finish_run(engine, run_id, "FAILED", error=str(exc)[:4000])
        raise


def read_run_exceptions(engine, run_id):
    with engine.connect() as connection:
        return pd.read_sql(text(
            "SELECT business_date, dataset_name, severity, rule_code, record_key, "
            "message, rejected_payload FROM dq_exception WHERE run_id=:run_id"),
            connection, params={"run_id": run_id})


def load_database(cfg, dq_exceptions, business_date=None):
    """Load one date or the full history and persist an independent run audit."""
    root = Path(cfg["root"])
    processed = root / cfg["paths"]["processed"]
    raw = root / cfg["paths"]["raw"]
    engine = initialize(root)
    run_id = _create_run(engine, business_date)
    loaded = 0

    try:
        with engine.begin() as connection:
            for table, filename in TABLE_FILES.items():
                frame = _read_csv(processed / filename, business_date)
                if table == "risk_metric_daily":
                    if business_date:
                        connection.execute(
                            text("DELETE FROM risk_metric_daily WHERE business_date=:date"),
                            {"date": business_date},
                        )
                    else:
                        connection.execute(text("DELETE FROM risk_metric_daily"))
                loaded += upsert_frame(connection, table, frame)

            now = datetime.now()
            for table, filename, source, date_column in RAW_FILES:
                frame = pd.read_csv(raw / filename)
                if business_date:
                    frame = frame[frame[date_column].astype(str) == business_date]
                rows = [
                    {
                        "run_id": run_id,
                        "source": source,
                        "payload": json.dumps(row, default=str, allow_nan=False),
                        "ingested_at": now,
                    }
                    for row in _records(frame)
                ]
                if rows:
                    connection.execute(
                        text(
                            f"INSERT INTO {table} "
                            "(run_id, source_system, payload, ingested_at) "
                            "VALUES (:run_id, :source, :payload, :ingested_at)"
                        ),
                        rows,
                    )

            selected_exceptions = dq_exceptions
            if business_date and "business_date" in selected_exceptions.columns:
                selected_exceptions = selected_exceptions[
                    selected_exceptions["business_date"].astype(str) == business_date
                ]
            if not selected_exceptions.empty:
                rows = []
                for row in _records(selected_exceptions):
                    rows.append(
                        {
                            "run_id": run_id,
                            "business_date": row.get("business_date"),
                            "dataset": row["dataset_name"],
                            "severity": row["severity"],
                            "rule": row["rule_code"],
                            "key": row["record_key"],
                            "message": row["message"],
                            "payload": row["rejected_payload"],
                            "created_at": now,
                        }
                    )
                connection.execute(
                    text(
                        "INSERT INTO dq_exception "
                        "(run_id, business_date, dataset_name, severity, rule_code, "
                        "record_key, message, rejected_payload, created_at) VALUES "
                        "(:run_id, :business_date, :dataset, :severity, :rule, "
                        ":key, :message, :payload, :created_at)"
                    ),
                    rows,
                )
    except Exception as exc:
        _finish_run(engine, run_id, "FAILED", error=str(exc)[:4000])
        raise

    _finish_run(engine, run_id, "SUCCESS", rows_loaded=loaded)
    return loaded

