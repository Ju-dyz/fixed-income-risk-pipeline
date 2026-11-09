"""Textbook cash-flow pricing and interest-rate risk measures."""

import numpy as np


class PricingModel:
    """Store shared pricing assumptions and expose reusable pricing methods.

    The methods delegate to the existing textbook functions below, so this
    wrapper changes code organization only, not any pricing formula or output.
    """

    def __init__(self, pricing_cfg, krd_tenors=(2, 5, 10, 30)):
        self.bond_frequency = pricing_cfg["bond_coupon_frequency"]
        self.swap_frequency = pricing_cfg["irs_fixed_frequency"]
        self.swap_direction = pricing_cfg["irs_direction"]
        self.bump_bp = pricing_cfg["risk_bump_bp"]
        self.krd_tenors = tuple(krd_tenors)

    def bond_metrics(self, face, coupon, ytm, years):
        """Return textbook price, Macaulay duration, modified duration and convexity."""
        return bond_metrics(face, coupon, ytm, years, self.bond_frequency)

    def bond_effective_metrics(self, face, coupon, ytm, years):
        """Return bond price, effective duration and effective convexity."""
        return bond_effective_metrics(
            face, coupon, ytm, years, self.bond_frequency, self.bump_bp
        )

    def swap_effective_metrics(self, face, fixed_rate, curve_rate, years):
        """Return swap quote and bump-and-reprice interest-rate measures."""
        return swap_effective_metrics(
            face, fixed_rate, curve_rate, years,
            self.swap_frequency, self.swap_direction, self.bump_bp,
        )

    def swap_quote(self, face, fixed_rate, curve_rate, years):
        """Return the configured receive-fixed or pay-fixed swap quote."""
        return swap_quote(
            face, fixed_rate, curve_rate, years,
            self.swap_frequency, self.swap_direction,
        )

    def key_rate_dv01(self, quantity, years, reprice):
        """Return dollar KRD values for the configured tenor nodes."""
        return key_rate_dv01(
            quantity, years, reprice, self.krd_tenors, self.bump_bp
        )


def bond_metrics(face, coupon, ytm, years, frequency=2):
    """Return full price, Macaulay duration, modified duration, and convexity."""
    periods = max(1, int(round(years * frequency)))
    times = np.arange(1, periods + 1) / frequency
    cash_flows = np.full(periods, face * coupon / frequency)
    cash_flows[-1] += face
    discount_factors = (1 + ytm / frequency) ** (-frequency * times)
    present_values = cash_flows * discount_factors
    full_price = present_values.sum()
    macaulay_duration = (times * present_values).sum() / full_price
    modified_duration = macaulay_duration / (1 + ytm / frequency)
    convexity = (
        (times * (times + 1 / frequency) * present_values).sum() / full_price
    ) / (1 + ytm / frequency) ** 2
    return (float(full_price), float(macaulay_duration),
            float(modified_duration), float(convexity))


def bond_effective_metrics(face, coupon, ytm, years, frequency=2, bump_bp=1.0):
    """Return price, effective duration, and effective convexity by bump-and-reprice."""
    bump = bump_bp / 10_000
    price, _, _, _ = bond_metrics(face, coupon, ytm, years, frequency)
    price_down = bond_metrics(face, coupon, max(ytm - bump, 1e-8), years, frequency)[0]
    price_up = bond_metrics(face, coupon, ytm + bump, years, frequency)[0]
    effective_duration = (price_down - price_up) / (2 * price * bump)
    effective_convexity = (price_down + price_up - 2 * price) / (price * bump**2)
    return float(price), float(effective_duration), float(effective_convexity)


def swap_quote(face, fixed_rate, curve_rate, years, fixed_frequency=2,
               direction="receive_fixed"):
    """Price a plain-vanilla swap as 100 plus fixed-leg PV minus floating-leg PV."""
    periods = max(1, int(round(years * fixed_frequency)))
    times = np.arange(1, periods + 1) / fixed_frequency
    discount_factors = (1 + curve_rate / fixed_frequency) ** (-fixed_frequency * times)
    fixed_leg = face * fixed_rate / fixed_frequency * discount_factors.sum()
    floating_leg = face * (1 - discount_factors[-1])
    net_value = fixed_leg - floating_leg
    if direction == "pay_fixed":
        net_value = -net_value
    return float(face + net_value)


def swap_effective_metrics(face, fixed_rate, curve_rate, years, fixed_frequency=2,
                           direction="receive_fixed", bump_bp=1.0):
    """Return swap quote, effective duration, convexity, and quote DV01."""
    bump = bump_bp / 10_000
    quote = swap_quote(face, fixed_rate, curve_rate, years, fixed_frequency, direction)
    quote_down = swap_quote(
        face, fixed_rate, max(curve_rate - bump, 1e-8), years,
        fixed_frequency, direction,
    )
    quote_up = swap_quote(
        face, fixed_rate, curve_rate + bump, years, fixed_frequency, direction,
    )
    effective_duration = (quote_down - quote_up) / (2 * quote * bump)
    effective_convexity = (quote_down + quote_up - 2 * quote) / (quote * bump**2)
    quote_dv01 = (quote_down - quote_up) / 2
    return (float(quote), float(effective_duration),
            float(effective_convexity), float(quote_dv01))


def key_rate_weights(years, tenors=(2, 5, 10, 30)):
    """Linear local-curve bump weights between adjacent key-rate tenors."""
    tenors = np.asarray(tenors, dtype=float)
    weights = np.zeros(len(tenors), dtype=float)
    if years <= tenors[0]:
        weights[0] = 1.0
    elif years >= tenors[-1]:
        weights[-1] = 1.0
    else:
        upper = int(np.searchsorted(tenors, years))
        lower = upper - 1
        span = tenors[upper] - tenors[lower]
        weights[lower] = (tenors[upper] - years) / span
        weights[upper] = (years - tenors[lower]) / span
    return weights


def key_rate_dv01(quantity, years, reprice, tenors=(2, 5, 10, 30), bump_bp=1.0):
    """Calculate dollar KRDs by applying one local key-rate bump at a time."""
    bump = bump_bp / 10_000
    values = []
    for weight in key_rate_weights(years, tenors):
        if weight == 0:
            values.append(0.0)
            continue
        price_down = reprice(-weight * bump)
        price_up = reprice(weight * bump)
        values.append(quantity / 100 * (price_down - price_up) / 2)
    return np.asarray(values, dtype=float)
