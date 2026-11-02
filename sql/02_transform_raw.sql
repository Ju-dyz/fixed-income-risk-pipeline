-- Standardize the current ingestion run inside MySQL. Parameters are supplied by Python.
-- Raw JSON remains unchanged for auditability and rejected records are written to dq_exception.

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.AsOfDate')), '%Y-%m-%d'),
       'positions', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate natural key - latest raw record retained', payload, :created_at
FROM (
  SELECT r.*, ROW_NUMBER() OVER (
    PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(payload,'$.AsOfDate')),
                 JSON_UNQUOTE(JSON_EXTRACT(payload,'$.Book')),
                 JSON_UNQUOTE(JSON_EXTRACT(payload,'$.SecurityID'))
    ORDER BY raw_id DESC) AS rn
  FROM raw_position r WHERE run_id=:run_id
) d WHERE rn > 1;

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), '%Y-%m-%d'),
       'market', 'ERROR', 'MISSING_QUOTE', CAST(raw_id AS CHAR),
       'Missing price or unmapped security identifier', payload, :created_at
FROM raw_market_data r
LEFT JOIN identifier_alias a
  ON a.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.sec_id'))
WHERE r.run_id=:run_id AND
 (NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.px')), 'null') IS NULL
  OR a.instrument_id IS NULL);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), '%Y-%m-%d'),
       'market', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate quote - latest raw record retained', payload, :created_at
FROM (
  SELECT r.*, ROW_NUMBER() OVER (
    PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')),
                 JSON_UNQUOTE(JSON_EXTRACT(payload,'$.sec_id')) ORDER BY raw_id DESC) rn
  FROM raw_market_data r WHERE run_id=:run_id
) d WHERE rn > 1;

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), '%Y-%m-%d'),
       'market', 'WARNING', 'STALE_QUOTE', CAST(raw_id AS CHAR),
       'Quote timestamp exceeds the configured stale-data threshold', payload, :created_at
FROM raw_market_data
WHERE run_id=:run_id AND
 DATEDIFF(STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), '%Y-%m-%d'),
          DATE(STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s'))) > :stale_days;

INSERT INTO position_daily
  (business_date, portfolio_id, instrument_id, quantity, clean_price, accrued_interest, market_value, source_system)
SELECT business_date, portfolio_id, instrument_id, quantity, clean_price, 0,
       quantity*clean_price/100, 'POS_SYS'
FROM (
  SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), '%Y-%m-%d') business_date,
         pa.portfolio_id, ia.instrument_id,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.ParAmount')) AS DECIMAL(22,4)) quantity,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Price')) AS DECIMAL(14,8)) clean_price,
         ROW_NUMBER() OVER (
           PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), pa.portfolio_id, ia.instrument_id
           ORDER BY r.raw_id DESC) rn
  FROM raw_position r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Book'))
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SecurityID'))
  WHERE r.run_id=:run_id
) s WHERE rn=1
ON DUPLICATE KEY UPDATE quantity=VALUES(quantity), clean_price=VALUES(clean_price),
 accrued_interest=VALUES(accrued_interest), market_value=VALUES(market_value), source_system=VALUES(source_system);

INSERT INTO market_data_daily
  (business_date, instrument_id, price, yield_rate, spread_rate, quote_timestamp, is_stale)
SELECT business_date, instrument_id, price, yield_rate, spread_rate, quote_timestamp,
       (DATEDIFF(business_date, DATE(quote_timestamp)) > :stale_days)
FROM (
  SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), '%Y-%m-%d') business_date,
         ia.instrument_id,
         CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')), 'null') AS DECIMAL(14,8)) price,
         CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.yield_pct')), 'null') AS DECIMAL(12,9))/100 yield_rate,
         CAST(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.spread_bps')), 'null') AS DECIMAL(12,9))/10000 spread_rate,
         STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s') quote_timestamp,
         ROW_NUMBER() OVER (PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), ia.instrument_id
                            ORDER BY r.raw_id DESC) rn
  FROM raw_market_data r
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.sec_id'))
  WHERE r.run_id=:run_id AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')), 'null') IS NOT NULL
) s WHERE rn=1 AND price IS NOT NULL
ON DUPLICATE KEY UPDATE price=VALUES(price), yield_rate=VALUES(yield_rate), spread_rate=VALUES(spread_rate),
 quote_timestamp=VALUES(quote_timestamp), is_stale=VALUES(is_stale);

INSERT INTO transaction_daily
  (trade_id, trade_timestamp, settlement_date, portfolio_id, instrument_id, quantity, trade_price, source_system)
SELECT JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')),
       STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), '%Y-%m-%d %H:%i:%s'),
       STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SETTLE_DT')), '%Y-%m-%d'),
       pa.portfolio_id, ia.instrument_id,
       CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.NOTIONAL')) AS DECIMAL(22,4)),
       CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_PX')) AS DECIMAL(14,8)), 'TRADE_SYS'
FROM raw_transaction r
JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.DESK_BOOK'))
JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.INTERNAL_SEC'))
WHERE r.run_id=:run_id
ON DUPLICATE KEY UPDATE trade_timestamp=VALUES(trade_timestamp), settlement_date=VALUES(settlement_date),
 quantity=VALUES(quantity), trade_price=VALUES(trade_price);

INSERT INTO nav_daily (business_date, portfolio_id, nav, source_system)
SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
       pa.portfolio_id, CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.nav')) AS DECIMAL(22,4)), 'NAV_SYS'
FROM raw_nav r JOIN portfolio_alias pa
 ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
WHERE r.run_id=:run_id
ON DUPLICATE KEY UPDATE nav=VALUES(nav), source_system=VALUES(source_system);

INSERT INTO actual_pnl_daily (business_date, portfolio_id, actual_pnl, source_system)
SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
       pa.portfolio_id, CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.actual_pnl')) AS DECIMAL(22,4)), 'PNL_SYS'
FROM raw_pnl r JOIN portfolio_alias pa
 ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
WHERE r.run_id=:run_id
ON DUPLICATE KEY UPDATE actual_pnl=VALUES(actual_pnl), source_system=VALUES(source_system);
