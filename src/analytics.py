from pathlib import Path

import pandas as pd

from .pricing import PricingModel
from .stress import calculate_stress
from .validation import validate_pricing
from .var_model import HistoricalVaRModel


PNL_COLUMNS = [
    "business_date",
    "portfolio_id",
    "nav",
    "realized_pnl",
    "hypothetical_pnl",
    "carry_pnl",
    "duration_pnl",
    "convexity_pnl",
    "rates_pnl",
    "spread_pnl",
    "trading_pnl",
    "residual_pnl",
]

RISK_COLUMNS = [
    "business_date", "portfolio_id", "instrument_id", "market_value",
    "macaulay_duration", "modified_duration", "duration_type",
    "convexity", "dv01", "cs01",
    "krd_2y", "krd_5y", "krd_10y", "krd_30y",
]


def _security_attribution(instrument_risk, positions, market, instruments, transactions):
    """Calculate P&L effects at security level using prior-day exposures."""
    # 1. Join risk, position, market and instrument data at security level.
    keys = ["business_date", "portfolio_id", "instrument_id"]
    market_keys = ["business_date", "instrument_id"]
    detail = instrument_risk.merge(
        positions[keys + ["quantity"]], on=keys, how="left", validate="one_to_one"
    ).merge(
        market[market_keys + ["yield_rate", "spread_rate", "price"]],
        on=market_keys, how="left", validate="many_to_one"
    ).merge(
        instruments[["instrument_id", "coupon_rate"]],
        on="instrument_id", how="left", validate="many_to_one"
    )
    detail = detail.sort_values(["portfolio_id", "instrument_id", "business_date"])
    security = detail.groupby(["portfolio_id", "instrument_id"], sort=False)

    # 2. Shift exposures so today's market moves use prior-day risk positions.
    for column in ("market_value", "quantity", "yield_rate", "spread_rate",
                   "dv01", "cs01", "convexity"):
        detail[f"previous_{column}"] = security[column].shift(1)

    detail["yield_change"] = detail["yield_rate"] - detail["previous_yield_rate"]
    detail["spread_change"] = detail["spread_rate"] - detail["previous_spread_rate"]
    detail["duration_pnl"] = -detail["previous_dv01"] * detail["yield_change"] * 10_000
    detail["convexity_pnl"] = (
        0.5 * detail["previous_market_value"] * detail["previous_convexity"]
        * detail["yield_change"].pow(2)
    )
    detail["spread_pnl"] = -detail["previous_cs01"] * detail["spread_change"] * 10_000
    detail["carry_pnl"] = detail["previous_quantity"] * detail["coupon_rate"] / 252
    component_columns = ["duration_pnl", "convexity_pnl", "spread_pnl", "carry_pnl"]
    detail[component_columns] = detail[component_columns].fillna(0.0)

    # 3. Calculate trading P&L from execution price versus closing price.
    if transactions is None or transactions.empty:
        trading = pd.DataFrame(columns=["business_date", "portfolio_id", "trading_pnl"])
    else:
        trades = transactions.copy()
        trades["business_date"] = pd.to_datetime(trades["trade_timestamp"]).dt.normalize()
        trades = trades.merge(
            market[market_keys + ["price"]], on=market_keys, how="left", validate="many_to_one"
        )
        trades["trading_pnl"] = trades["quantity"] * (trades["price"] - trades["trade_price"]) / 100
        trading = trades.groupby(["business_date", "portfolio_id"], as_index=False)["trading_pnl"].sum()

    # 4. Aggregate security effects into daily portfolio attribution.
    daily = detail.groupby(["business_date", "portfolio_id"], as_index=False).agg(
        market_value=("market_value", "sum"), dv01=("dv01", "sum"), cs01=("cs01", "sum"),
        krd_2y=("krd_2y", "sum"), krd_10y=("krd_10y", "sum"),
        mbs_market_value=("mbs_market_value", "sum"), carry_pnl=("carry_pnl", "sum"),
        duration_pnl=("duration_pnl", "sum"), convexity_pnl=("convexity_pnl", "sum"),
        spread_pnl=("spread_pnl", "sum"),
    )
    daily = daily.merge(trading, on=["business_date", "portfolio_id"], how="left")
    daily["trading_pnl"] = daily["trading_pnl"].fillna(0.0)
    daily["rates_pnl"] = daily["duration_pnl"] + daily["convexity_pnl"]
    daily["hypothetical_pnl"] = daily[
        ["carry_pnl", "duration_pnl", "convexity_pnl", "spread_pnl", "trading_pnl"]
    ].sum(axis=1)
    return daily


def _attach_actuals(daily, nav, actual_pnl):
    """Attach NAV and actual P&L, then return attribution with residual P&L."""
    if actual_pnl is None or actual_pnl.empty:
        raise ValueError("Actual P&L source is required; NAV-difference fallback is disabled")

    frame = daily.copy()
    if nav is None or nav.empty:
        frame["nav"] = frame["market_value"] + 10_000_000
    else:
        frame = frame.merge(
            nav[["business_date", "portfolio_id", "nav"]],
            on=["business_date", "portfolio_id"], how="left", validate="one_to_one"
        )
        frame["nav"] = frame["nav"].fillna(frame["market_value"] + 10_000_000)
    frame = frame.merge(
        actual_pnl[["business_date", "portfolio_id", "actual_pnl"]],
        on=["business_date", "portfolio_id"], how="left", validate="one_to_one"
    )
    missing_pnl = int(frame["actual_pnl"].isna().sum())
    if missing_pnl:
        raise ValueError(f"Missing standardized actual P&L inputs: {missing_pnl}")

    frame = frame.rename(columns={"actual_pnl": "realized_pnl"})
    frame["residual_pnl"] = frame["realized_pnl"] - frame["hypothetical_pnl"]
    return frame


def _calculate_instrument_risk(positions, market, instruments, cfg):
    """Return security-level textbook duration, convexity, DV01, CS01 and KRD."""
    frame = positions.merge(market, on=["business_date", "instrument_id"], how="inner")
    frame = frame.merge(instruments, on="instrument_id", how="inner", suffixes=("", "_ref"))
    pricing_model = PricingModel(
        cfg["pricing"], krd_tenors=cfg["risk"]["krd_tenors_years"]
    )
    rows = []
    for row in frame.itertuples(index=False):
        date = pd.Timestamp(row.business_date)
        years = max((pd.Timestamp(row.maturity_date) - date).days / 365.25, 0.05)
        if row.instrument_type == "IRS":
            macaulay_duration = None
            duration_type = "EFFECTIVE"
            _, duration, convexity, _ = pricing_model.swap_effective_metrics(
                100, row.coupon_rate, row.yield_rate, years
            )
            reprice = lambda shift: pricing_model.swap_quote(
                100, row.coupon_rate, max(row.yield_rate + shift, 1e-8), years
            )
        elif row.instrument_type == "MBS":
            macaulay_duration = None
            duration_type = "EFFECTIVE"
            _, duration, convexity = pricing_model.bond_effective_metrics(
                100, row.coupon_rate, row.yield_rate, years
            )
            reprice = lambda shift: pricing_model.bond_metrics(
                100, row.coupon_rate, max(row.yield_rate + shift, 1e-8),
                years,
            )[0]
        else:
            _, macaulay_duration, duration, convexity = pricing_model.bond_metrics(
                100, row.coupon_rate, row.yield_rate, years
            )
            duration_type = "MODIFIED"
            reprice = lambda shift: pricing_model.bond_metrics(
                100, row.coupon_rate, max(row.yield_rate + shift, 1e-8),
                years,
            )[0]
        dv01 = row.market_value * duration * 0.0001
        cs01 = dv01 if row.instrument_type in ("CORP", "MBS") else 0.0
        krd = pricing_model.key_rate_dv01(row.quantity, years, reprice)
        rows.append({
            "business_date": row.business_date, "portfolio_id": row.portfolio_id,
            "instrument_id": row.instrument_id, "market_value": row.market_value,
            "macaulay_duration": macaulay_duration,
            "modified_duration": duration, "duration_type": duration_type,
            "convexity": convexity, "dv01": dv01,
            "cs01": cs01, "krd_2y": krd[0], "krd_5y": krd[1],
            "krd_10y": krd[2], "krd_30y": krd[3],
            "mbs_market_value": row.market_value if row.instrument_type == "MBS" else 0.0,
        })
    return pd.DataFrame(rows)


def calculate(cfg, positions=None, market=None, nav=None, actual_pnl=None, transactions=None):
    """Return risk, P&L, portfolio summary and stress DataFrames; also save CSVs."""
    # 1. Load any inputs that were not supplied by the pipeline.
    output = Path(cfg["root"]) / cfg["paths"]["processed"]
    if market is None:
        market = pd.read_csv(output / "market_clean.csv", parse_dates=["business_date"])
    if positions is None:
        positions = pd.read_csv(output / "position_clean.csv", parse_dates=["business_date"])
    if nav is None and (output / "nav_clean.csv").exists():
        nav = pd.read_csv(output / "nav_clean.csv", parse_dates=["business_date"])
    if actual_pnl is None and (output / "actual_pnl_clean.csv").exists():
        actual_pnl = pd.read_csv(output / "actual_pnl_clean.csv", parse_dates=["business_date"])
    if transactions is None and (output / "transaction_clean.csv").exists():
        transactions = pd.read_csv(output / "transaction_clean.csv", parse_dates=["trade_timestamp"])
    instruments = pd.read_csv(output / "ground_truth_instrument.csv", parse_dates=["maturity_date"])
    positions["business_date"] = pd.to_datetime(positions["business_date"])
    market["business_date"] = pd.to_datetime(market["business_date"])
    if nav is not None:
        nav["business_date"] = pd.to_datetime(nav["business_date"])
    if actual_pnl is not None:
        actual_pnl["business_date"] = pd.to_datetime(actual_pnl["business_date"])
    if transactions is not None and not transactions.empty:
        transactions["trade_timestamp"] = pd.to_datetime(transactions["trade_timestamp"])
    # 2. Calculate instrument risk and portfolio P&L attribution.
    instrument_risk = _calculate_instrument_risk(positions, market, instruments, cfg)
    pricing_validation, pricing_failures = validate_pricing(
        instrument_risk, market, instruments, cfg
    )
    pricing_validation.to_csv(output / "pricing_validation.csv", index=False)
    pricing_failures.to_csv(output / "pricing_validation_details.csv", index=False)
    daily = _security_attribution(instrument_risk, positions, market, instruments, transactions)
    analytics = _attach_actuals(daily, nav, actual_pnl)
    # 3. Run classical historical-simulation VaR and backtesting.
    var_model = HistoricalVaRModel(lookback=cfg["risk"]["var_lookback"])
    var_results, var_scenarios = var_model.calculate(instrument_risk, market)
    var_results = var_model.backtest(analytics)
    analytics = analytics.merge(
        var_results, on=["business_date", "portfolio_id"], how="left", validate="one_to_one"
    )

    pnl = analytics[PNL_COLUMNS]
    summary_columns = [
        "business_date",
        "portfolio_id",
        "nav",
        "market_value",
        "dv01",
        "cs01",
        "realized_pnl",
        "hypothetical_pnl",
        "residual_pnl",
        "var_95",
        "var_99",
        "pnl_95_exception",
        "pnl_99_exception",
    ]
    summary = analytics[summary_columns]

    # 4. Run stress testing and save all report-ready outputs.
    stress = calculate_stress(analytics, cfg["stress"])

    for name, frame in {
        "risk_metric": instrument_risk[RISK_COLUMNS],
        "pnl_nav": pnl,
        "portfolio_risk": summary,
        "stress_results": stress,
    }.items():
        frame.to_csv(output / f"{name}.csv", index=False)
    var_scenarios.to_csv(output / "var_historical_scenarios.csv", index=False)

    return instrument_risk[RISK_COLUMNS], pnl, summary, stress

