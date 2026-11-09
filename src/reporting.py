from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill


def _read_for_date(path, business_date):
    frame = pd.read_csv(path)
    return frame[frame["business_date"].astype(str) == business_date]


def _format_workbook(workbook):
    """Apply report formatting, filters, number formats and print settings."""
    header_fill = PatternFill("solid", fgColor="1F4E78")
    red_fill = PatternFill("solid", fgColor="F4CCCC")
    amber_fill = PatternFill("solid", fgColor="FFF2CC")
    amount_columns = {
        "nav", "market_value", "dv01", "cs01", "krd_2y", "krd_5y",
        "krd_10y", "krd_30y", "realized_pnl", "hypothetical_pnl",
        "carry_pnl", "duration_pnl", "convexity_pnl", "rates_pnl",
        "spread_pnl", "trading_pnl", "residual_pnl", "var_95", "var_99",
        "stress_pnl", "stress_loss", "stress_limit", "max_abs_difference",
        "tolerance",
    }
    integer_columns = {
        "pnl_95_exception", "pnl_99_exception", "stress_limit_breach",
        "failed_rows",
    }
    for sheet in workbook.worksheets:
        header_row = 4 if sheet.title == "Portfolio Summary" else 1
        sheet.freeze_panes = f"A{header_row + 1}"
        sheet.auto_filter.ref = (
            f"A{header_row}:{sheet.cell(sheet.max_row, sheet.max_column).coordinate}"
        )
        for cell in sheet[header_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
        if sheet.title == "Portfolio Summary":
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = header_fill
        for column in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column) + 2
            sheet.column_dimensions[column[0].column_letter].width = min(width, 35)
        headers = {cell.value: cell.column for cell in sheet[header_row]}
        for header, column in headers.items():
            if header in amount_columns:
                for row in range(header_row + 1, sheet.max_row + 1):
                    sheet.cell(row, column).number_format = "#,##0.00"
            elif header in integer_columns:
                for row in range(header_row + 1, sheet.max_row + 1):
                    sheet.cell(row, column).number_format = "0"
        for flag in ("pnl_95_exception", "pnl_99_exception", "stress_limit_breach"):
            if flag in headers:
                for row in range(header_row + 1, sheet.max_row + 1):
                    if sheet.cell(row, headers[flag]).value in (True, 1):
                        sheet.cell(row, headers[flag]).fill = red_fill
        if "severity" in headers:
            for row in range(header_row + 1, sheet.max_row + 1):
                cell = sheet.cell(row, headers["severity"])
                if cell.value == "ERROR":
                    cell.fill = red_fill
                elif cell.value == "WARNING":
                    cell.fill = amber_fill
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = f"{header_row}:{header_row}"


def create_report(cfg, business_date=None):
    root = Path(cfg["root"])
    output = root / cfg["paths"]["processed"]
    report_dir = root / cfg["paths"]["reports"]
    report_dir.mkdir(parents=True, exist_ok=True)

    all_summary = pd.read_csv(output / "portfolio_risk.csv")
    date = business_date or str(all_summary["business_date"].max())
    if date not in set(all_summary["business_date"].astype(str)):
        raise ValueError(f"No analytics are available for {date}")

    summary = _read_for_date(output / "portfolio_risk.csv", date)
    risk = _read_for_date(output / "risk_metric.csv", date)
    pnl = _read_for_date(output / "pnl_nav.csv", date)
    stress = _read_for_date(output / "stress_results.csv", date)
    exceptions = pd.read_csv(output / "dq_exceptions.csv")
    validation = pd.read_csv(output / "pricing_validation.csv")
    krd = risk.groupby("portfolio_id")[
        ["krd_2y", "krd_5y", "krd_10y", "krd_30y", "cs01"]
    ].sum().reset_index()

    report = report_dir / f"fixed_income_risk_{date}.xlsx"
    with pd.ExcelWriter(report, engine="openpyxl") as writer:
        pd.DataFrame(
            {"Notice": [cfg["project"]["synthetic_label"]], "As of": [date]}
        ).to_excel(writer, sheet_name="Portfolio Summary", index=False)
        summary.to_excel(
            writer, sheet_name="Portfolio Summary", index=False, startrow=3
        )
        krd.to_excel(writer, sheet_name="DV01 CS01", index=False)
        pnl.to_excel(writer, sheet_name="PnL Attribution", index=False)
        summary[
            [
                "portfolio_id",
                "var_95",
                "var_99",
                "pnl_95_exception",
                "pnl_99_exception",
            ]
        ].to_excel(writer, sheet_name="VaR Monitoring", index=False)
        stress.to_excel(writer, sheet_name="Stress Tests", index=False)
        validation.to_excel(writer, sheet_name="Pricing Validation", index=False)
        exceptions.to_excel(writer, sheet_name="Data Quality", index=False)
        _format_workbook(writer.book)
    return report

