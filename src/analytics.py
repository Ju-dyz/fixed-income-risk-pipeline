from pathlib import Path

import numpy as np
import pandas as pd

from .pricing import bond_metrics, key_rate_weights


PNL_COLUMNS = [
    "business_date",
    "portfolio_id",
    "nav",
    "realized_pnl",
    "hypothetical_pnl",
    "carry_pnl",
    "rates_pnl",
    "spread_pnl",
    "residual_pnl",
]

RISK_COLUMNS = [
    "business_date", "portfolio_id", "instrument_id", "market_value",
    "modified_duration", "convexity", "dv01", "cs01",
    "krd_2y", "krd_5y", "krd_10y", "krd_30y",
]


def _portfolio_pnl(group, market, average_return, lookback, nav_source=None, pnl_source=None):
    frame = group.sort_values("business_date").copy()
    dates = frame["business_date"]
    yield_change = market.groupby("business_date")["yield_rate"].mean().diff()
    spread_change = market.groupby("business_date")["spread_rate"].mean().diff()

    if nav_source is not None and not nav_source.empty:
        frame = frame.merge(nav_source[["business_date", "nav"]], on="business_date", how="left")
    else:
        frame["nav"] = frame["market_value"] + 10_000_000
    if pnl_source is not None and not pnl_source.empty:
        frame = frame.merge(pnl_source[["business_date", "actual_pnl"]], on="business_date", how="left")
        frame["realized_pnl"] = frame["actual_pnl"].fillna(0)
        frame = frame.drop(columns="actual_pnl")
    else:
        frame["realized_pnl"] = frame["nav"].diff().fillna(0)
    frame["hypothetical_pnl"] = (
        frame["market_value"].shift(1)
        * average_return.reindex(dates).to_numpy()
    )
    frame["carry_pnl"] = (
        frame["market_value"].shift(1).fillna(frame["market_value"]) * 0.04 / 252
    )
    frame["rates_pnl"] = (
        -frame["dv01"].shift(1).fillna(frame["dv01"])
        * yield_change.reindex(dates).fillna(0).to_numpy()
        * 10_000
    )
    frame["spread_pnl"] = (
        -frame["cs01"].shift(1).fillna(frame["cs01"])
        * spread_change.reindex(dates).fillna(0).to_numpy()
        * 10_000
    )
    frame["residual_pnl"] = frame["realized_pnl"] - frame[
        ["carry_pnl", "rates_pnl", "spread_pnl"]
    ].sum(axis=1)

    history = frame["hypothetical_pnl"].shift(1)
    frame["var_95"] = -history.rolling(lookback, min_periods=20).quantile(0.05)
    frame["var_99"] = -history.rolling(lookback, min_periods=20).quantile(0.01)
    frame["pnl_95_exception"] = np.where(
        frame["var_95"].notna(), frame["realized_pnl"] < -frame["var_95"], None
    )
    frame["pnl_99_exception"] = np.where(
        frame["var_99"].notna(), frame["realized_pnl"] < -frame["var_99"], None
    )
    return frame


def _calculate_instrument_risk(positions, market, instruments):
    frame = positions.merge(market, on=["business_date", "instrument_id"], how="inner")
    frame = frame.merge(instruments, on="instrument_id", how="inner", suffixes=("", "_ref"))
    rows = []
    for row in frame.itertuples(index=False):
        date = pd.Timestamp(row.business_date)
        years = max((pd.Timestamp(row.maturity_date) - date).days / 365.25, 0.05)
        if row.instrument_type == "IRS":
            duration = min(years, 10) * 0.78
            convexity = duration**2 * 0.55
        else:
            _, duration, convexity = bond_metrics(100, row.coupon_rate, row.yield_rate, years)
        dv01 = row.market_value * duration * 0.0001
        cs01 = dv01 if row.instrument_type in ("CORP", "MBS") else 0.0
        krd = key_rate_weights(years) * dv01
        rows.append({
            "business_date": row.business_date, "portfolio_id": row.portfolio_id,
            "instrument_id": row.instrument_id, "market_value": row.market_value,
            "modified_duration": duration, "convexity": convexity, "dv01": dv01,
            "cs01": cs01, "krd_2y": krd[0], "krd_5y": krd[1],
            "krd_10y": krd[2], "krd_30y": krd[3],
            "mbs_market_value": row.market_value if row.instrument_type == "MBS" else 0.0,
        })
    return pd.DataFrame(rows)


def calculate(cfg, positions=None, market=None, nav=None, actual_pnl=None):
    output = Path(cfg["root"]) / cfg["paths"]["processed"]
    if market is None:
        market = pd.read_csv(output / "market_clean.csv", parse_dates=["business_date"])
    if positions is None:
        positions = pd.read_csv(output / "position_clean.csv", parse_dates=["business_date"])
    instruments = pd.read_csv(output / "ground_truth_instrument.csv", parse_dates=["maturity_date"])
    positions["business_date"] = pd.to_datetime(positions["business_date"])
    market["business_date"] = pd.to_datetime(market["business_date"])
    if nav is not None:
        nav["business_date"] = pd.to_datetime(nav["business_date"])
    if actual_pnl is not None:
        actual_pnl["business_date"] = pd.to_datetime(actual_pnl["business_date"])
    instrument_risk = _calculate_instrument_risk(positions, market, instruments)
    daily = instrument_risk.groupby(
        ["business_date", "portfolio_id"], as_index=False
    ).agg(
        market_value=("market_value", "sum"),
        dv01=("dv01", "sum"),
        cs01=("cs01", "sum"),
        krd_2y=("krd_2y", "sum"),
        krd_10y=("krd_10y", "sum"),
        mbs_market_value=("mbs_market_value", "sum"),
    )

    average_return = (
        market.pivot(index="business_date", columns="instrument_id", values="price")
        .sort_index()
        .pct_change(fill_method=None)
        .mean(axis=1)
    )
    lookback = cfg["risk"]["var_lookback"]
    frames = []
    for portfolio_id, group in daily.groupby("portfolio_id"):
        nav_group = None if nav is None else nav[nav["portfolio_id"] == portfolio_id]
        pnl_group = None if actual_pnl is None else actual_pnl[actual_pnl["portfolio_id"] == portfolio_id]
        frames.append(_portfolio_pnl(group, market, average_return, lookback, nav_group, pnl_group))
    analytics = pd.concat(frames, ignore_index=True)

    pnl = analytics[PNL_COLUMNS]
    summary_columns = [
        "business_date",
        "portfolio_id",
        "nav",
        "market_value",
        "dv01",
        "cs01",
        "var_95",
        "var_99",
        "pnl_95_exception",
        "pnl_99_exception",
    ]
    summary = analytics[summary_columns]

    stress_cfg = cfg["stress"]
    shocks = stress_cfg["rate_shocks_bp"]
    volatility_shock = stress_cfg["volatility_end"] - stress_cfg["volatility_start"]
    stress_rows = []
    for row in analytics.itertuples(index=False):
        rate_pnl = -row.krd_2y * shocks["krd_2y"] - row.krd_10y * shocks["krd_10y"]
        vega_proxy = (row.mbs_market_value / 100_000_000) * stress_cfg["mbs_vega_proxy_per_100mm"]
        volatility_pnl = -vega_proxy * volatility_shock
        stress_rows.append({
            "business_date": row.business_date,
            "portfolio_id": row.portfolio_id,
            "scenario_name": stress_cfg["scenario_name"],
            "stress_pnl": rate_pnl + volatility_pnl,
        })
    stress = pd.DataFrame(stress_rows)

    for name, frame in {
        "risk_metric": instrument_risk[RISK_COLUMNS],
        "pnl_nav": pnl,
        "portfolio_risk": summary,
        "stress_results": stress,
    }.items():
        frame.to_csv(output / f"{name}.csv", index=False)

    return instrument_risk[RISK_COLUMNS], pnl, summary, stress

