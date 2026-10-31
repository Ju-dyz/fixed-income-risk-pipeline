import argparse
import json

from .analytics import calculate
from .config import settings
from .etl import normalize
from .generate_data import generate
from .reporting import create_report


def run(business_date=None, load_db=False):
    cfg = settings()
    generated = generate(cfg)
    positions, market, _, exceptions = normalize(cfg)
    calculate(cfg)
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
        from .database import load_database

        result["database_rows_upserted"] = load_database(
            cfg, exceptions, business_date
        )

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

