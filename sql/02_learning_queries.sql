USE fixed_income_risk_pipeline;

-- JOIN reference, position, and risk data using stable internal keys.
SELECT p.business_date, pf.portfolio_name, i.instrument_type,
       SUM(p.market_value) market_value, SUM(r.dv01) dv01, SUM(r.cs01) cs01
FROM position_daily p
JOIN portfolio pf ON pf.portfolio_id=p.portfolio_id
JOIN instrument i ON i.instrument_id=p.instrument_id
JOIN risk_metric_daily r ON r.business_date=p.business_date
 AND r.portfolio_id=p.portfolio_id AND r.instrument_id=p.instrument_id
GROUP BY p.business_date, pf.portfolio_name, i.instrument_type;

-- Review unresolved/error observations before publishing a report.
SELECT business_date, rule_code, severity, COUNT(*) exception_count
FROM dq_exception GROUP BY business_date, rule_code, severity
ORDER BY business_date DESC, severity DESC;

-- Backtesting exception rate: compare observed frequency with nominal tail probability.
SELECT portfolio_id, COUNT(*) observations,
 SUM(pnl_95_exception) exceptions_95, SUM(pnl_99_exception) exceptions_99
FROM portfolio_risk_daily WHERE var_95 IS NOT NULL GROUP BY portfolio_id;

