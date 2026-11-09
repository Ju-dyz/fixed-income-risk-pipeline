"""Independent reconciliation checks for calculated pricing risk measures."""

import numpy as np
import pandas as pd


def _result(check, passed, failed_rows, max_difference=None, tolerance=None):
    """Build one compact validation-summary record."""
    return {
        "check": check,
        "status": "PASS" if passed else "FAIL",
        "failed_rows": int(failed_rows),
        "max_abs_difference": max_difference,
        "tolerance": tolerance,
    }


def validate_pricing(instrument_risk, market, instruments, cfg):
    """Return pricing validation summary and row-level failure details.

    The checks independently reconcile DV01, key-rate DV01, duration conventions,
    and CS01 rules without changing any calculated analytics result.
    """
    keys = ["business_date", "instrument_id"]
    risk = instrument_risk.copy()
    market_data = market[keys + ["yield_rate"]].copy()
    reference = instruments[["instrument_id", "instrument_type"]].copy()
    risk["business_date"] = pd.to_datetime(risk["business_date"])
    market_data["business_date"] = pd.to_datetime(market_data["business_date"])
    detail = risk.merge(market_data, on=keys, how="left", validate="many_to_one")
    detail = detail.merge(reference, on="instrument_id", how="left", validate="many_to_one")

    summaries = []
    failures = []

    # 1. Reconcile reported DV01 with market value and reported duration.
    detail["expected_dv01"] = (
        detail["market_value"] * detail["modified_duration"] * 0.0001
    )
    detail["dv01_difference"] = detail["dv01"] - detail["expected_dv01"]
    dv01_ok = np.isclose(
        detail["dv01"], detail["expected_dv01"], rtol=1e-10, atol=1e-6
    )
    summaries.append(_result(
        "DV01 = market value x duration x 1bp",
        bool(dv01_ok.all()), (~dv01_ok).sum(),
        float(detail["dv01_difference"].abs().max()), 1e-6,
    ))
    failures.append(_failure_rows(detail, ~dv01_ok, "DV01_FORMULA", "dv01_difference"))

    # 2. A parallel DV01 should reconcile with the sum of local key-rate DV01s.
    krd_columns = ["krd_2y", "krd_5y", "krd_10y", "krd_30y"]
    detail["krd_total"] = detail[krd_columns].sum(axis=1)
    detail["krd_difference"] = detail["krd_total"] - detail["dv01"]
    krd_ok = np.isclose(
        detail["krd_total"], detail["dv01"], rtol=1e-4, atol=0.05
    )
    summaries.append(_result(
        "Sum of KRDs approximately equals DV01",
        bool(krd_ok.all()), (~krd_ok).sum(),
        float(detail["krd_difference"].abs().max()), "atol=0.05; rtol=0.0001",
    ))
    failures.append(_failure_rows(detail, ~krd_ok, "KRD_RECONCILIATION", "krd_difference"))

    # 3. Reconcile textbook modified duration for UST and corporate bonds.
    bond_mask = detail["instrument_type"].isin(["UST", "CORP"])
    frequency = cfg["pricing"]["bond_coupon_frequency"]
    detail["expected_modified_duration"] = (
        detail["macaulay_duration"] / (1 + detail["yield_rate"] / frequency)
    )
    modified_ok = ~bond_mask | np.isclose(
        detail["modified_duration"], detail["expected_modified_duration"],
        rtol=1e-10, atol=1e-8,
    )
    modified_difference = (
        detail.loc[bond_mask, "modified_duration"]
        - detail.loc[bond_mask, "expected_modified_duration"]
    ).abs()
    summaries.append(_result(
        "Modified duration = Macaulay / (1 + yield/frequency)",
        bool(modified_ok.all()), (~modified_ok).sum(),
        float(modified_difference.max()), 1e-8,
    ))
    detail["duration_difference"] = (
        detail["modified_duration"] - detail["expected_modified_duration"]
    )
    failures.append(_failure_rows(
        detail, ~modified_ok, "MODIFIED_DURATION_FORMULA", "duration_difference"
    ))

    # 4. Confirm each product uses its intended duration convention.
    expected_type = np.where(bond_mask, "MODIFIED", "EFFECTIVE")
    duration_type_ok = detail["duration_type"].eq(expected_type)
    macaulay_ok = (
        (bond_mask & detail["macaulay_duration"].notna())
        | (~bond_mask & detail["macaulay_duration"].isna())
    )
    convention_ok = duration_type_ok & macaulay_ok
    summaries.append(_result(
        "Duration convention matches instrument type",
        bool(convention_ok.all()), (~convention_ok).sum(),
    ))
    failures.append(_failure_rows(
        detail, ~convention_ok, "DURATION_CONVENTION", None
    ))

    # 5. Confirm CS01 is applied only to corporate bonds and MBS instruments.
    spread_mask = detail["instrument_type"].isin(["CORP", "MBS"])
    expected_cs01 = np.where(spread_mask, detail["dv01"], 0.0)
    cs01_ok = np.isclose(detail["cs01"], expected_cs01, rtol=1e-10, atol=1e-6)
    detail["cs01_difference"] = detail["cs01"] - expected_cs01
    summaries.append(_result(
        "CS01 rule matches instrument type",
        bool(cs01_ok.all()), (~cs01_ok).sum(),
        float(detail["cs01_difference"].abs().max()), 1e-6,
    ))
    failures.append(_failure_rows(detail, ~cs01_ok, "CS01_RULE", "cs01_difference"))

    failure_frames = [frame for frame in failures if not frame.empty]
    failure_detail = (
        pd.concat(failure_frames, ignore_index=True)
        if failure_frames
        else pd.DataFrame(columns=[
            "check_code", "business_date", "portfolio_id", "instrument_id",
            "instrument_type", "difference",
        ])
    )
    return pd.DataFrame(summaries), failure_detail


def _failure_rows(frame, mask, check_code, difference_column):
    """Return identifiers and differences for rows that fail one check."""
    columns = [
        "business_date", "portfolio_id", "instrument_id", "instrument_type"
    ]
    failed = frame.loc[mask, columns].copy()
    failed.insert(0, "check_code", check_code)
    failed["difference"] = (
        frame.loc[mask, difference_column].to_numpy()
        if difference_column else np.nan
    )
    return failed
