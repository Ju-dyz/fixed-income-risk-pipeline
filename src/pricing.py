import numpy as np

def bond_metrics(face, coupon, ytm, years, frequency=2):
    """Flat-YTM fixed-coupon approximation; returns dirty price per face, mod duration, convexity."""
    n = max(1, int(round(years * frequency)))
    t = np.arange(1, n + 1) / frequency
    cash = np.full(n, face * coupon / frequency); cash[-1] += face
    disc = (1 + ytm / frequency) ** (-frequency * t)
    pv = cash * disc; price = pv.sum()
    macaulay = (t * pv).sum() / price
    modified = macaulay / (1 + ytm / frequency)
    convexity = ((t * (t + 1 / frequency) * pv).sum() / price) / (1 + ytm / frequency) ** 2
    return float(price), float(modified), float(convexity)

def key_rate_weights(years, tenors=(2, 5, 10, 30)):
    d = np.abs(np.asarray(tenors, float) - years)
    if d.min() == 0: w = (d == 0).astype(float)
    else:
        w = 1 / np.maximum(d, .25); w = w / w.sum()
    return w

