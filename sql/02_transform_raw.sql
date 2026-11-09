-- Standardize the current ingestion run inside MySQL. Parameters are supplied by Python.
-- Checks use canonical portfolio/security identifiers before valid records are upserted.

-- Position: required fields and duplicates after canonical identifier mapping.
INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), 'null'), '%Y-%m-%d'),
       'positions', 'ERROR', 'REQUIRED_FIELD', CAST(r.raw_id AS CHAR),
       'Missing required position field or unmapped portfolio/security identifier', r.payload, :created_at
FROM raw_position r
LEFT JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Book'))
LEFT JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SecurityID'))
WHERE r.run_id=:run_id AND (
  STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), 'null'), '%Y-%m-%d') IS NULL
  OR pa.portfolio_id IS NULL OR ia.instrument_id IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.ParAmount')), 'null') IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Price')), 'null') IS NULL
);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, business_date, 'positions', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate canonical position key - latest raw record retained', payload, :created_at
FROM (
  SELECT r.raw_id, r.payload,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), '%Y-%m-%d') business_date,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), '%Y-%m-%d'),
                        pa.portfolio_id, ia.instrument_id ORDER BY r.raw_id DESC) rn
  FROM raw_position r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Book'))
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SecurityID'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.ParAmount')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Price')), 'null') IS NOT NULL
) d WHERE rn>1;

-- Market: all NOT NULL table fields, canonical duplicates, and stale quotes.
INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), 'null'), '%Y-%m-%d'),
       'market', 'ERROR', 'MISSING_QUOTE', CAST(r.raw_id AS CHAR),
       'Missing required market field or unmapped security identifier', r.payload, :created_at
FROM raw_market_data r
LEFT JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.sec_id'))
WHERE r.run_id=:run_id AND (
  STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), 'null'), '%Y-%m-%d') IS NULL
  OR ia.instrument_id IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')), 'null') IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.yield_pct')), 'null') IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.spread_bps')), 'null') IS NULL
  OR STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s') IS NULL
);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, business_date, 'market', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate canonical market key - latest raw record retained', payload, :created_at
FROM (
  SELECT r.raw_id, r.payload,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), '%Y-%m-%d') business_date,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), '%Y-%m-%d'),
                        ia.instrument_id ORDER BY r.raw_id DESC) rn
  FROM raw_market_data r
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.sec_id'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.yield_pct')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.spread_bps')), 'null') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s') IS NOT NULL
) d WHERE rn>1;

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), '%Y-%m-%d'),
       'market', 'WARNING', 'STALE_QUOTE', CAST(raw_id AS CHAR),
       'Quote timestamp exceeds the configured stale-data threshold', payload, :created_at
FROM raw_market_data
WHERE run_id=:run_id AND
 DATEDIFF(
   STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.valuation_dt')), 'null'), '%Y-%m-%d'),
   DATE(STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s'))
 ) > :stale_days;

-- Transaction: required fields and duplicate trade IDs.
INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       DATE(STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), 'null'), '%Y-%m-%d %H:%i:%s')),
       'transactions', 'ERROR', 'REQUIRED_FIELD', CAST(r.raw_id AS CHAR),
       'Missing required transaction field or unmapped portfolio/security identifier', r.payload, :created_at
FROM raw_transaction r
LEFT JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.DESK_BOOK'))
LEFT JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.INTERNAL_SEC'))
WHERE r.run_id=:run_id AND (
  NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')), 'null') IS NULL
  OR STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), 'null'), '%Y-%m-%d %H:%i:%s') IS NULL
  OR STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SETTLE_DT')), 'null'), '%Y-%m-%d') IS NULL
  OR pa.portfolio_id IS NULL OR ia.instrument_id IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.NOTIONAL')), 'null') IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_PX')), 'null') IS NULL
);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, business_date, 'transactions', 'WARNING', 'DUPLICATE', trade_id,
       'Duplicate trade ID - latest raw record retained', payload, :created_at
FROM (
  SELECT r.raw_id, r.payload, JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')) trade_id,
         DATE(STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), '%Y-%m-%d %H:%i:%s')) business_date,
         ROW_NUMBER() OVER (
           PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')) ORDER BY r.raw_id DESC) rn
  FROM raw_transaction r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.DESK_BOOK'))
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.INTERNAL_SEC'))
  WHERE r.run_id=:run_id
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')), 'null') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), 'null'), '%Y-%m-%d %H:%i:%s') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SETTLE_DT')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.NOTIONAL')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_PX')), 'null') IS NOT NULL
) d WHERE rn>1;

-- NAV and actual P&L: required fields and canonical portfolio/date duplicates.
INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d'),
       'nav', 'ERROR', 'REQUIRED_FIELD', CAST(r.raw_id AS CHAR),
       'Missing required NAV field or unmapped portfolio identifier', r.payload, :created_at
FROM raw_nav r
LEFT JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
WHERE r.run_id=:run_id AND (
  STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NULL
  OR pa.portfolio_id IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.nav')), 'null') IS NULL
);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, business_date, 'nav', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate canonical NAV key - latest raw record retained', payload, :created_at
FROM (
  SELECT r.raw_id, r.payload,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d') business_date,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
                        pa.portfolio_id ORDER BY r.raw_id DESC) rn
  FROM raw_nav r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.nav')), 'null') IS NOT NULL
) d WHERE rn>1;

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id,
       STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d'),
       'actual_pnl', 'ERROR', 'REQUIRED_FIELD', CAST(r.raw_id AS CHAR),
       'Missing required actual P&L field or unmapped portfolio identifier', r.payload, :created_at
FROM raw_pnl r
LEFT JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
WHERE r.run_id=:run_id AND (
  STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NULL
  OR pa.portfolio_id IS NULL
  OR NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.actual_pnl')), 'null') IS NULL
);

INSERT INTO dq_exception
  (run_id, business_date, dataset_name, severity, rule_code, record_key, message, rejected_payload, created_at)
SELECT :run_id, business_date, 'actual_pnl', 'WARNING', 'DUPLICATE', CAST(raw_id AS CHAR),
       'Duplicate canonical actual P&L key - latest raw record retained', payload, :created_at
FROM (
  SELECT r.raw_id, r.payload,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d') business_date,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
                        pa.portfolio_id ORDER BY r.raw_id DESC) rn
  FROM raw_pnl r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.actual_pnl')), 'null') IS NOT NULL
) d WHERE rn>1;

-- Valid, canonical records are retained once and upserted into standardized tables.
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
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), '%Y-%m-%d'),
                        pa.portfolio_id, ia.instrument_id ORDER BY r.raw_id DESC) rn
  FROM raw_position r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Book'))
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SecurityID'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.AsOfDate')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.ParAmount')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.Price')), 'null') IS NOT NULL
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
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')) AS DECIMAL(14,8)) price,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.yield_pct')) AS DECIMAL(12,9))/100 yield_rate,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.spread_bps')) AS DECIMAL(12,9))/10000 spread_rate,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.quote_time')), '%Y-%m-%d %H:%i:%s') quote_timestamp,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), '%Y-%m-%d'),
                        ia.instrument_id ORDER BY r.raw_id DESC) rn
  FROM raw_market_data r
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.sec_id'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.valuation_dt')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.px')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.yield_pct')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.spread_bps')), 'null') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.quote_time')), 'null'), '%Y-%m-%d %H:%i:%s') IS NOT NULL
) s WHERE rn=1
ON DUPLICATE KEY UPDATE price=VALUES(price), yield_rate=VALUES(yield_rate), spread_rate=VALUES(spread_rate),
 quote_timestamp=VALUES(quote_timestamp), is_stale=VALUES(is_stale);

INSERT INTO transaction_daily
  (trade_id, trade_timestamp, settlement_date, portfolio_id, instrument_id, quantity, trade_price, source_system)
SELECT trade_id, trade_timestamp, settlement_date, portfolio_id, instrument_id, quantity, trade_price, 'TRADE_SYS'
FROM (
  SELECT JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')) trade_id,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), '%Y-%m-%d %H:%i:%s') trade_timestamp,
         STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SETTLE_DT')), '%Y-%m-%d') settlement_date,
         pa.portfolio_id, ia.instrument_id,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.NOTIONAL')) AS DECIMAL(22,4)) quantity,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_PX')) AS DECIMAL(14,8)) trade_price,
         ROW_NUMBER() OVER (
           PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')) ORDER BY r.raw_id DESC) rn
  FROM raw_transaction r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.DESK_BOOK'))
  JOIN identifier_alias ia ON ia.source_identifier=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.INTERNAL_SEC'))
  WHERE r.run_id=:run_id
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.trade_id')), 'null') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_TS')), 'null'), '%Y-%m-%d %H:%i:%s') IS NOT NULL
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.SETTLE_DT')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.NOTIONAL')), 'null') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.EXEC_PX')), 'null') IS NOT NULL
) s WHERE rn=1
ON DUPLICATE KEY UPDATE trade_timestamp=VALUES(trade_timestamp), settlement_date=VALUES(settlement_date),
 portfolio_id=VALUES(portfolio_id), instrument_id=VALUES(instrument_id), quantity=VALUES(quantity),
 trade_price=VALUES(trade_price), source_system=VALUES(source_system);

INSERT INTO nav_daily (business_date, portfolio_id, nav, source_system)
SELECT business_date, portfolio_id, nav, 'NAV_SYS'
FROM (
  SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d') business_date,
         pa.portfolio_id, CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.nav')) AS DECIMAL(22,4)) nav,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
                        pa.portfolio_id ORDER BY r.raw_id DESC) rn
  FROM raw_nav r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.nav')), 'null') IS NOT NULL
) s WHERE rn=1
ON DUPLICATE KEY UPDATE nav=VALUES(nav), source_system=VALUES(source_system);

INSERT INTO actual_pnl_daily (business_date, portfolio_id, actual_pnl, source_system)
SELECT business_date, portfolio_id, actual_pnl, 'PNL_SYS'
FROM (
  SELECT STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d') business_date,
         pa.portfolio_id,
         CAST(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.actual_pnl')) AS DECIMAL(22,4)) actual_pnl,
         ROW_NUMBER() OVER (
           PARTITION BY STR_TO_DATE(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), '%Y-%m-%d'),
                        pa.portfolio_id ORDER BY r.raw_id DESC) rn
  FROM raw_pnl r
  JOIN portfolio_alias pa ON pa.source_name=JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.portfolio'))
  WHERE r.run_id=:run_id
    AND STR_TO_DATE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.business_date')), 'null'), '%Y-%m-%d') IS NOT NULL
    AND NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.payload,'$.actual_pnl')), 'null') IS NOT NULL
) s WHERE rn=1
ON DUPLICATE KEY UPDATE actual_pnl=VALUES(actual_pnl), source_system=VALUES(source_system);
