import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

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
)


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


def _create_run(engine, business_date):
    run_id = str(uuid.uuid4())
    params = {
        "run_id": run_id,
        "business_date": business_date,
        "mode": "DAILY" if business_date else "FULL",
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

