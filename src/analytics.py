from pathlib import Path

import numpy as np
import pandas as pd


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

SCENARIOS = {
    "Parallel +100bp": (100, 0),
    "Parallel -100bp": (-100, 0),
    "Credit +150bp": (0, 150),
    "2008-style rates/credit": (-75, 300),
}


def _portfolio_pnl(group, market, average_return, lookback):
    frame = group.sort_values("business_date").copy()
    dates = frame["business_date"]
    yield_change = market.groupby("business_date")["yield_rate"].mean().diff()
    spread_change = market.groupby("business_date")["spread_rate"].mean().diff()

    frame["nav"] = frame["market_value"] + 10_000_000
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


def calculate(cfg):
    output = Path(cfg["root"]) / cfg["paths"]["processed"]
    risk = pd.read_csv(output / "ground_truth_risk.csv", parse_dates=["business_date"])
    market = pd.read_csv(output / "market_clean.csv", parse_dates=["business_date"])
    positions = pd.read_csv(output / "position_clean.csv", parse_dates=["business_date"])

    keys = ["business_date", "portfolio_id", "instrument_id"]
    valid_quotes = market[["business_date", "instrument_id"]].drop_duplicates()
    instrument_risk = (
        risk.merge(positions[keys], on=keys, how="inner")
        .merge(valid_quotes, on=["business_date", "instrument_id"], how="inner")
    )
    daily = instrument_risk.groupby(
        ["business_date", "portfolio_id"], as_index=False
    ).agg(
        market_value=("market_value", "sum"),
        dv01=("dv01", "sum"),
        cs01=("cs01", "sum"),
    )

    average_return = (
        market.pivot(index="business_date", columns="instrument_id", values="price")
        .sort_index()
        .pct_change(fill_method=None)
        .mean(axis=1)
    )
    lookback = cfg["risk"]["var_lookback"]
    frames = [
        _portfolio_pnl(group, market, average_return, lookback)
        for _, group in daily.groupby("portfolio_id")
    ]
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

    stress_rows = []
    for row in summary.itertuples(index=False):
        for scenario, (rate_shock, spread_shock) in SCENARIOS.items():
            stress_rows.append(
                {
                    "business_date": row.business_date,
                    "portfolio_id": row.portfolio_id,
                    "scenario_name": scenario,
                    "stress_pnl": -row.dv01 * rate_shock - row.cs01 * spread_shock,
                }
            )
    stress = pd.DataFrame(stress_rows)

    for name, frame in {
        "risk_metric": instrument_risk,
        "pnl_nav": pnl,
        "portfolio_risk": summary,
        "stress_results": stress,
    }.items():
        frame.to_csv(output / f"{name}.csv", index=False)

    return instrument_risk, pnl, summary, stress

