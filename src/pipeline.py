import argparse
import json
from pathlib import Path

from .analytics import calculate
from .config import settings
from .generate_data import generate
from .reporting import create_report


class RiskPipeline:
    """Orchestrate generation, SQL processing, analytics and reporting."""

    def __init__(self, cfg=None, database_client=None):
        self.cfg = cfg or settings()
        self.database = database_client
        self.status = "INITIALIZED"
        self.business_date = None

    def run(self, business_date=None, load_db=False):
        """Execute the pipeline and return a run summary dictionary."""
        self.status = "RUNNING"
        self.business_date = business_date
        try:
            result = self._execute(business_date, load_db)
            self.status = "SUCCESS"
            return result
        except Exception:
            self.status = "FAILED"
            raise

    def _execute(self, business_date, load_db):
        """Run the existing workflow without changing its calculations or outputs."""
        if not load_db:
            raise ValueError(
                "RiskPipeline requires --load-db; SQL is the authoritative "
                "standardization path. Use src.run_analytics for analytics-only runs."
            )
        cfg = self.cfg
        generated = generate(cfg)

        # 1. Use MySQL as the single authoritative standardization path.
        from .database import DatabaseClient

        self.database = self.database or DatabaseClient(cfg)
        standardized = self.database.ingest_and_standardize(business_date)
        positions = standardized["position_daily"]
        market = standardized["market_data_daily"]
        nav = standardized["nav_daily"]
        actual_pnl = standardized["actual_pnl_daily"]
        transactions = standardized["transaction_daily"]
        output = Path(cfg["root"]) / cfg["paths"]["processed"]
        positions.to_csv(output / "position_clean.csv", index=False)
        market.to_csv(output / "market_clean.csv", index=False)
        nav.to_csv(output / "nav_clean.csv", index=False)
        actual_pnl.to_csv(output / "actual_pnl_clean.csv", index=False)
        exceptions = self.database.read_run_exceptions()
        exceptions.to_csv(output / "dq_exceptions.csv", index=False)

        # 2. Calculate risk, P&L, VaR/backtesting and stress results.
        frames = calculate(cfg, positions, market, nav, actual_pnl, transactions)

        # 3. Persist analytics when database mode is enabled.
        database_rows = self.database.persist_analytics(frames)

        # 4. Create the final Excel report and return run metadata.
        report = create_report(cfg, business_date)
        result = {
            "synthetic": True,
            "generated": generated,
            "clean_positions": len(positions),
            "clean_market": len(market),
            "dq_exceptions": len(exceptions),
            "report": str(report),
        }
        result["raw_rows_ingested"] = self.database.ingested_rows
        result["database_analytics_rows_upserted"] = database_rows
        result["pipeline_run_id"] = self.database.run_id
        print(json.dumps(result, indent=2, default=str))
        return result


def run(business_date=None, load_db=False):
    """Backward-compatible functional entry point for the class-based pipeline."""
    return RiskPipeline().run(business_date, load_db)


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

