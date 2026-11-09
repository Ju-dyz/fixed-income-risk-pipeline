"""Named historical and hypothetical portfolio stress scenarios."""

import pandas as pd


def calculate_stress(analytics, stress_cfg):
    """Return portfolio stress P&L, loss, limit and breach flag for each date."""
    # 1. Read the named scenario shocks from configuration.
    shocks = stress_cfg["rate_shocks_bp"]
    volatility_shock = stress_cfg["volatility_end"] - stress_cfg["volatility_start"]
    rows = []
    for row in analytics.itertuples(index=False):
        # 2. Apply rate and volatility shocks to each portfolio's current exposure.
        rate_pnl = -row.krd_2y * shocks["krd_2y"] - row.krd_10y * shocks["krd_10y"]
        vega_proxy = (
            row.mbs_market_value / 100_000_000
        ) * stress_cfg["mbs_vega_proxy_per_100mm"]
        volatility_pnl = -vega_proxy * volatility_shock
        stress_pnl = rate_pnl + volatility_pnl
        stress_loss = max(-stress_pnl, 0.0)
        stress_limit = row.nav * stress_cfg["stress_limit_pct_nav"]
        # 3. Compare the positive stress loss amount with its NAV-based limit.
        rows.append({
            "business_date": row.business_date,
            "portfolio_id": row.portfolio_id,
            "scenario_name": stress_cfg["scenario_name"],
            "nav": row.nav,
            "stress_pnl": stress_pnl,
            "stress_loss": stress_loss,
            "stress_limit": stress_limit,
            "stress_limit_breach": int(stress_loss > stress_limit),
        })
    return pd.DataFrame(rows)
