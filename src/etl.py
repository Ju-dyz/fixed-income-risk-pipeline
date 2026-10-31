import json
from pathlib import Path

import numpy as np
import pandas as pd


POSITION_COLUMNS = [
    "business_date",
    "portfolio_id",
    "instrument_id",
    "quantity",
    "clean_price",
    "accrued_interest",
    "market_value",
    "source_system",
]

MARKET_COLUMNS = [
    "business_date",
    "instrument_id",
    "price",
    "yield_rate",
    "spread_rate",
    "quote_timestamp",
    "is_stale",
]

TRANSACTION_COLUMNS = [
    "trade_id",
    "trade_timestamp",
    "settlement_date",
    "portfolio_id",
    "instrument_id",
    "quantity",
    "trade_price",
    "source_system",
]


def _lookups(instruments, portfolios):
    instrument_lookup = {}
    for row in instruments.itertuples(index=False):
        for identifier in (row.instrument_id, row.cusip, row.isin):
            instrument_lookup[str(identifier)] = row.instrument_id

    portfolio_lookup = {}
    for row in portfolios.itertuples(index=False):
        aliases = {
            row.portfolio_id,
            row.portfolio_name,
            row.portfolio_name.lower().replace(" ", "_"),
        }
        portfolio_lookup.update({alias: row.portfolio_id for alias in aliases})
    return instrument_lookup, portfolio_lookup


def _exception(exceptions, dataset, rule, key, message, row=None, severity="ERROR"):
    business_date = None if row is None else row.get("business_date")
    payload = None if row is None else json.dumps(row.astype(str).to_dict())
    exceptions.append(
        {
            "business_date": business_date,
            "dataset_name": dataset,
            "severity": severity,
            "rule_code": rule,
            "record_key": str(key),
            "message": message,
            "rejected_payload": payload,
        }
    )


def _normalize_positions(frame, instrument_lookup, portfolio_lookup, exceptions):
    frame = frame.rename(
        columns={
            "AsOfDate": "business_date",
            "Book": "portfolio_id",
            "SecurityID": "instrument_id",
            "ParAmount": "quantity",
            "Price": "clean_price",
        }
    )
    frame["business_date"] = pd.to_datetime(
        frame["business_date"], errors="coerce"
    ).dt.date
    frame["portfolio_id"] = frame["portfolio_id"].map(portfolio_lookup)
    frame["instrument_id"] = (
        frame["instrument_id"].astype(str).map(instrument_lookup)
    )

    duplicates = frame.duplicated(
        ["business_date", "portfolio_id", "instrument_id"], keep="last"
    )
    for index in frame.index[duplicates]:
        _exception(
            exceptions,
            "positions",
            "DUPLICATE",
            index,
            "Duplicate natural key; last record retained",
            frame.loc[index],
            "WARNING",
        )
    frame = frame[~duplicates].copy()

    required = [
        "business_date",
        "portfolio_id",
        "instrument_id",
        "quantity",
        "clean_price",
    ]
    rejected = frame[required].isna().any(axis=1)
    for index in frame.index[rejected]:
        _exception(
            exceptions,
            "positions",
            "REQUIRED_FIELD",
            index,
            "Required field missing after mapping",
            frame.loc[index],
        )
    frame = frame[~rejected].copy()
    frame["accrued_interest"] = 0.0
    frame["market_value"] = frame["quantity"] * frame["clean_price"] / 100
    frame["source_system"] = "POS_SYS"
    return frame[POSITION_COLUMNS]


def _normalize_market(frame, instrument_lookup, stale_days, exceptions):
    frame = frame.rename(
        columns={
            "valuation_dt": "business_date",
            "sec_id": "instrument_id",
            "px": "price",
            "yield_pct": "yield_rate",
            "spread_bps": "spread_rate",
            "quote_time": "quote_timestamp",
        }
    )
    frame["business_date"] = pd.to_datetime(
        frame["business_date"], errors="coerce"
    ).dt.date
    frame["quote_timestamp"] = pd.to_datetime(
        frame["quote_timestamp"], errors="coerce"
    )
    frame["instrument_id"] = (
        frame["instrument_id"].astype(str).map(instrument_lookup)
    )
    frame["yield_rate"] /= 100
    frame["spread_rate"] /= 10_000

    duplicates = frame.duplicated(["business_date", "instrument_id"], keep="last")
    for index in frame.index[duplicates]:
        _exception(
            exceptions,
            "market",
            "DUPLICATE",
            index,
            "Duplicate quote; last record retained",
            frame.loc[index],
            "WARNING",
        )
    frame = frame[~duplicates].copy()

    required = [
        "business_date",
        "instrument_id",
        "price",
        "yield_rate",
        "quote_timestamp",
    ]
    rejected = frame[required].isna().any(axis=1)
    for index in frame.index[rejected]:
        _exception(
            exceptions,
            "market",
            "MISSING_QUOTE",
            index,
            "Missing or unmapped quote rejected",
            frame.loc[index],
        )
    frame = frame[~rejected].copy()

    quote_dates = frame["quote_timestamp"].dt.date.to_numpy(dtype="datetime64[D]")
    business_dates = np.asarray(frame["business_date"], dtype="datetime64[D]")
    age = np.busday_count(quote_dates, business_dates)
    frame["is_stale"] = age > stale_days
    for index, business_age in zip(frame.index[frame["is_stale"]], age[frame["is_stale"]]):
        key = f"{frame.at[index, 'business_date']}|{frame.at[index, 'instrument_id']}"
        _exception(
            exceptions,
            "market",
            "STALE_QUOTE",
            key,
            f"Quote age {business_age} business days",
            frame.loc[index],
            "WARNING",
        )
    return frame[MARKET_COLUMNS]


def _normalize_transactions(frame, instrument_lookup, portfolio_lookup, exceptions):
    frame = frame.rename(
        columns={
            "EXEC_TS": "trade_timestamp",
            "SETTLE_DT": "settlement_date",
            "DESK_BOOK": "portfolio_id",
            "INTERNAL_SEC": "instrument_id",
            "NOTIONAL": "quantity",
            "EXEC_PX": "trade_price",
        }
    )
    frame["trade_timestamp"] = pd.to_datetime(
        frame["trade_timestamp"], errors="coerce"
    )
    frame["settlement_date"] = pd.to_datetime(
        frame["settlement_date"], errors="coerce"
    ).dt.date
    frame["business_date"] = frame["trade_timestamp"].dt.date
    frame["portfolio_id"] = frame["portfolio_id"].map(portfolio_lookup)
    frame["instrument_id"] = (
        frame["instrument_id"].astype(str).map(instrument_lookup)
    )

    duplicates = frame.duplicated("trade_id", keep="last")
    required = [column for column in TRANSACTION_COLUMNS if column != "source_system"]
    rejected = frame[required].isna().any(axis=1)
    for index in frame.index[duplicates]:
        _exception(
            exceptions,
            "transactions",
            "DUPLICATE",
            frame.at[index, "trade_id"],
            "Duplicate trade ID; last record retained",
            frame.loc[index],
            "WARNING",
        )
    for index in frame.index[rejected & ~duplicates]:
        _exception(
            exceptions,
            "transactions",
            "REQUIRED_FIELD",
            index,
            "Required transaction field missing after mapping",
            frame.loc[index],
        )
    return frame[~duplicates & ~rejected][TRANSACTION_COLUMNS].copy()


def normalize(cfg):
    root = Path(cfg["root"])
    raw = root / cfg["paths"]["raw"]
    output = root / cfg["paths"]["processed"]
    output.mkdir(parents=True, exist_ok=True)

    instruments = pd.read_csv(raw / "security_master.csv")
    portfolios = pd.read_csv(raw / "portfolio_master.csv")
    instrument_lookup, portfolio_lookup = _lookups(instruments, portfolios)
    exceptions = []

    positions = _normalize_positions(
        pd.read_csv(raw / "positions_source_a.csv"),
        instrument_lookup,
        portfolio_lookup,
        exceptions,
    )
    market = _normalize_market(
        pd.read_csv(raw / "market_source_b.csv"),
        instrument_lookup,
        cfg["risk"]["stale_business_days"],
        exceptions,
    )
    transactions = _normalize_transactions(
        pd.read_csv(raw / "transactions_source_c.csv"),
        instrument_lookup,
        portfolio_lookup,
        exceptions,
    )
    exception_frame = pd.DataFrame(exceptions)

    positions.to_csv(output / "position_clean.csv", index=False)
    market.to_csv(output / "market_clean.csv", index=False)
    transactions.to_csv(output / "transaction_clean.csv", index=False)
    exception_frame.to_csv(output / "dq_exceptions.csv", index=False)
    return positions, market, transactions, exception_frame

