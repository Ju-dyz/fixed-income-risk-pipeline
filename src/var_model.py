"""Classical historical-simulation VaR and backtesting."""

import numpy as np
import pandas as pd


class HistoricalVaRModel:
    """Manage the historical window and retain VaR simulation results."""

    def __init__(self, lookback=60):
        self.lookback = lookback
        self.var_results = None
        self.simulation_results = None

    def calculate(self, instrument_risk, market):
        """Return portfolio VaR and scenario P&L while storing both results."""
        self.var_results, self.simulation_results = calculate_historical_simulation_var(
            instrument_risk, market, lookback=self.lookback
        )
        return self.var_results, self.simulation_results

    def backtest(self, actual_pnl):
        """Return VaR results with 95% and 99% breach flags."""
        if self.var_results is None:
            raise RuntimeError("Run calculate() before backtest().")
        self.var_results = backtest_var(self.var_results, actual_pnl)
        return self.var_results


def _historical_security_shocks(market):
    """Return daily yield and spread changes for every instrument."""
    shocks = market[["business_date", "instrument_id", "yield_rate", "spread_rate"]].copy()
    shocks = shocks.sort_values(["instrument_id", "business_date"])
    grouped = shocks.groupby("instrument_id", sort=False)
    shocks["yield_change"] = grouped["yield_rate"].diff()
    shocks["spread_change"] = grouped["spread_rate"].diff()
    return shocks.dropna(subset=["yield_change", "spread_change"])


def calculate_historical_simulation_var(instrument_risk, market, lookback=60):
    """Revalue prior-day exposures under 60 historical security-level shocks.

    Returns one VaR row per valuation date/portfolio plus the underlying
    simulated P&L scenarios used to calculate the empirical percentiles.
    """
    # 1. Build security-level historical yield and spread shock matrices.
    risk = instrument_risk.copy()
    risk["business_date"] = pd.to_datetime(risk["business_date"])
    shocks = _historical_security_shocks(market)
    shocks["business_date"] = pd.to_datetime(shocks["business_date"])
    yield_shocks = shocks.pivot(
        index="business_date", columns="instrument_id", values="yield_change"
    ).sort_index()
    spread_shocks = shocks.pivot(
        index="business_date", columns="instrument_id", values="spread_change"
    ).sort_index()

    # 2. Reprice each fixed prior-day portfolio under the latest historical shocks.
    var_rows = []
    simulation_rows = []
    for portfolio_id, portfolio_risk in risk.groupby("portfolio_id"):
        portfolio_dates = sorted(portfolio_risk["business_date"].unique())
        for index, valuation_date in enumerate(portfolio_dates):
            var_row = {
                "business_date": valuation_date,
                "portfolio_id": portfolio_id,
                "var_95": np.nan,
                "var_99": np.nan,
            }
            if index == 0:
                var_rows.append(var_row)
                continue

            exposure_date = portfolio_dates[index - 1]
            exposures = portfolio_risk[
                portfolio_risk["business_date"] == exposure_date
            ].set_index("instrument_id")
            available_dates = yield_shocks.index[yield_shocks.index < valuation_date]
            scenario_dates = available_dates[-lookback:]
            if len(scenario_dates) < lookback:
                var_rows.append(var_row)
                continue

            instruments = exposures.index
            yield_matrix = yield_shocks.reindex(
                index=scenario_dates, columns=instruments
            ).fillna(0.0).to_numpy()
            spread_matrix = spread_shocks.reindex(
                index=scenario_dates, columns=instruments
            ).fillna(0.0).to_numpy()

            # 3. Aggregate instrument effects into one simulated portfolio P&L per shock.
            duration_pnl = -yield_matrix @ exposures["dv01"].to_numpy() * 10_000
            convexity_pnl = 0.5 * (yield_matrix ** 2) @ (
                exposures["market_value"].to_numpy()
                * exposures["convexity"].to_numpy()
            )
            spread_pnl = -spread_matrix @ exposures["cs01"].to_numpy() * 10_000
            simulated_pnl = duration_pnl + convexity_pnl + spread_pnl

            # 4. Convert lower-tail P&L percentiles into positive VaR loss amounts.
            var_row["var_95"] = max(0.0, -float(np.quantile(simulated_pnl, 0.05)))
            var_row["var_99"] = max(0.0, -float(np.quantile(simulated_pnl, 0.01)))
            var_rows.append(var_row)
            simulation_rows.extend({
                "business_date": valuation_date,
                "portfolio_id": portfolio_id,
                "exposure_date": exposure_date,
                "scenario_date": scenario_date,
                "simulated_pnl": pnl,
            } for scenario_date, pnl in zip(scenario_dates, simulated_pnl))

    return pd.DataFrame(var_rows), pd.DataFrame(simulation_rows)


def backtest_var(var_results, actual_pnl):
    """Compare current actual P&L with positive VaR loss amounts and flag breaches."""
    result = var_results.merge(
        actual_pnl[["business_date", "portfolio_id", "realized_pnl"]],
        on=["business_date", "portfolio_id"], how="left", validate="one_to_one",
    )
    result["pnl_95_exception"] = np.where(
        result["var_95"].notna(),
        (result["realized_pnl"] < -result["var_95"]).astype(int),
        None,
    )
    result["pnl_99_exception"] = np.where(
        result["var_99"].notna(),
        (result["realized_pnl"] < -result["var_99"]).astype(int),
        None,
    )
    return result.drop(columns="realized_pnl")
