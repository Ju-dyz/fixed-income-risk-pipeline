import argparse
import json
from pathlib import Path

from .analytics import calculate
from .config import settings
from .etl import normalize
from .generate_data import generate
from .reporting import create_report


def run(business_date=None, load_db=False):
    cfg = settings()
    generated = generate(cfg)
    if load_db:
        from .database import (ingest_and_standardize, persist_analytics,
                               read_run_exceptions, read_standardized)

        engine, run_id, ingested = ingest_and_standardize(cfg, business_date)
        standardized = read_standardized(engine)
        positions = standardized["position_daily"]
        market = standardized["market_data_daily"]
        nav = standardized["nav_daily"]
        actual_pnl = standardized["actual_pnl_daily"]
        output = Path(cfg["root"]) / cfg["paths"]["processed"]
        positions.to_csv(output / "position_clean.csv", index=False)
        market.to_csv(output / "market_clean.csv", index=False)
        standardized["nav_daily"].to_csv(output / "nav_clean.csv", index=False)
        standardized["actual_pnl_daily"].to_csv(output / "actual_pnl_clean.csv", index=False)
        exceptions = read_run_exceptions(engine, run_id)
        exceptions.to_csv(output / "dq_exceptions.csv", index=False)
        frames = calculate(cfg, positions, market, nav, actual_pnl)
        database_rows = persist_analytics(engine, run_id, frames, ingested)
    else:
        positions, market, _, exceptions = normalize(cfg)
        frames = calculate(cfg)
        database_rows = None

    report = create_report(cfg, business_date)

    result = {
        "synthetic": True,
        "generated": generated,
        "clean_positions": len(positions),
        "clean_market": len(market),
        "dq_exceptions": len(exceptions),
        "report": str(report),
    }

    if load_db:
        result["raw_rows_ingested"] = ingested
        result["database_analytics_rows_upserted"] = database_rows
        result["pipeline_run_id"] = run_id

    print(json.dumps(result, indent=2, default=str))
    return result


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--date", dest="business_date")
    mode.add_argument("--full-history", action="store_true")
    parser.add_argument("--load-db", action="store_true")
    args = parser.parse_args()
    run(args.business_date, args.load_db)


if __name__ == "__main__":
    main()

