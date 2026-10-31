USE fixed_income_risk_pipeline;

CREATE TABLE IF NOT EXISTS pipeline_run (
  run_id CHAR(36) PRIMARY KEY, business_date DATE NULL, mode VARCHAR(20) NOT NULL,
  started_at DATETIME(6) NOT NULL, finished_at DATETIME(6) NULL,
  status ENUM('RUNNING','SUCCESS','FAILED') NOT NULL, rows_loaded INT NOT NULL DEFAULT 0,
  error_message TEXT NULL, INDEX ix_run_date (business_date, started_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS instrument (
  instrument_id VARCHAR(20) PRIMARY KEY, cusip VARCHAR(9) NULL UNIQUE, isin VARCHAR(12) NULL UNIQUE,
  instrument_type ENUM('UST','CORP','IRS','MBS') NOT NULL, issuer VARCHAR(100) NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'USD', coupon_rate DECIMAL(10,8) NOT NULL,
  maturity_date DATE NOT NULL, issue_date DATE NOT NULL, rating VARCHAR(4) NULL,
  sector VARCHAR(40) NOT NULL, face_value DECIMAL(18,2) NOT NULL DEFAULT 100,
  synthetic_flag BOOLEAN NOT NULL DEFAULT TRUE, CHECK (maturity_date > issue_date),
  INDEX ix_instrument_type_maturity (instrument_type, maturity_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS portfolio (
  portfolio_id VARCHAR(20) PRIMARY KEY, portfolio_name VARCHAR(100) NOT NULL UNIQUE,
  strategy VARCHAR(80) NOT NULL, synthetic_flag BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS identifier_alias (
  source_system VARCHAR(30) NOT NULL, source_identifier VARCHAR(50) NOT NULL,
  instrument_id VARCHAR(20) NOT NULL, PRIMARY KEY (source_system, source_identifier),
  CONSTRAINT fk_alias_instrument FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS portfolio_alias (
  source_system VARCHAR(30) NOT NULL, source_name VARCHAR(100) NOT NULL,
  portfolio_id VARCHAR(20) NOT NULL, PRIMARY KEY (source_system, source_name),
  CONSTRAINT fk_palias_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS raw_position (
  raw_id BIGINT AUTO_INCREMENT PRIMARY KEY, run_id CHAR(36) NOT NULL, source_system VARCHAR(30) NOT NULL,
  payload JSON NOT NULL, ingested_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_rawpos_run FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id), INDEX ix_rawpos_run (run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS raw_market_data (
  raw_id BIGINT AUTO_INCREMENT PRIMARY KEY, run_id CHAR(36) NOT NULL, source_system VARCHAR(30) NOT NULL,
  payload JSON NOT NULL, ingested_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_rawmkt_run FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id), INDEX ix_rawmkt_run (run_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS position_daily (
  business_date DATE NOT NULL, portfolio_id VARCHAR(20) NOT NULL, instrument_id VARCHAR(20) NOT NULL,
  quantity DECIMAL(22,4) NOT NULL, clean_price DECIMAL(14,8) NOT NULL, accrued_interest DECIMAL(14,8) NOT NULL,
  market_value DECIMAL(22,4) NOT NULL, source_system VARCHAR(30) NOT NULL,
  PRIMARY KEY (business_date, portfolio_id, instrument_id),
  CONSTRAINT fk_pos_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id),
  CONSTRAINT fk_pos_instrument FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id),
  INDEX ix_pos_instrument_date (instrument_id, business_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS transaction_daily (
  trade_id VARCHAR(30) PRIMARY KEY, trade_timestamp DATETIME(6) NOT NULL, settlement_date DATE NOT NULL,
  portfolio_id VARCHAR(20) NOT NULL, instrument_id VARCHAR(20) NOT NULL,
  quantity DECIMAL(22,4) NOT NULL, trade_price DECIMAL(14,8) NOT NULL, source_system VARCHAR(30) NOT NULL,
  CONSTRAINT fk_tx_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id),
  CONSTRAINT fk_tx_instrument FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id),
  INDEX ix_tx_settle_port (settlement_date, portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS market_data_daily (
  business_date DATE NOT NULL, instrument_id VARCHAR(20) NOT NULL, price DECIMAL(14,8) NOT NULL,
  yield_rate DECIMAL(12,9) NOT NULL, spread_rate DECIMAL(12,9) NOT NULL,
  quote_timestamp DATETIME(6) NOT NULL, is_stale BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (business_date, instrument_id),
  CONSTRAINT fk_mkt_instrument FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id),
  INDEX ix_mkt_quote_ts (quote_timestamp)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS risk_metric_daily (
  business_date DATE NOT NULL, portfolio_id VARCHAR(20) NOT NULL, instrument_id VARCHAR(20) NOT NULL,
  market_value DECIMAL(22,4) NOT NULL, modified_duration DECIMAL(14,8) NOT NULL,
  convexity DECIMAL(18,8) NOT NULL, dv01 DECIMAL(22,6) NOT NULL, cs01 DECIMAL(22,6) NOT NULL,
  krd_2y DECIMAL(22,6) NOT NULL, krd_5y DECIMAL(22,6) NOT NULL,
  krd_10y DECIMAL(22,6) NOT NULL, krd_30y DECIMAL(22,6) NOT NULL,
  PRIMARY KEY (business_date, portfolio_id, instrument_id),
  CONSTRAINT fk_risk_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id),
  CONSTRAINT fk_risk_instrument FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS pnl_nav_daily (
  business_date DATE NOT NULL, portfolio_id VARCHAR(20) NOT NULL, nav DECIMAL(22,4) NOT NULL,
  realized_pnl DECIMAL(22,4) NOT NULL, hypothetical_pnl DECIMAL(22,4) NULL,
  carry_pnl DECIMAL(22,4) NOT NULL, rates_pnl DECIMAL(22,4) NOT NULL,
  spread_pnl DECIMAL(22,4) NOT NULL, residual_pnl DECIMAL(22,4) NOT NULL,
  PRIMARY KEY (business_date, portfolio_id),
  CONSTRAINT fk_pnl_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS portfolio_risk_daily (
  business_date DATE NOT NULL, portfolio_id VARCHAR(20) NOT NULL, nav DECIMAL(22,4) NOT NULL,
  market_value DECIMAL(22,4) NOT NULL, dv01 DECIMAL(22,6) NOT NULL, cs01 DECIMAL(22,6) NOT NULL,
  var_95 DECIMAL(22,4) NULL, var_99 DECIMAL(22,4) NULL, pnl_95_exception BOOLEAN NULL,
  pnl_99_exception BOOLEAN NULL, PRIMARY KEY (business_date, portfolio_id),
  CONSTRAINT fk_prisk_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS stress_result_daily (
  business_date DATE NOT NULL, portfolio_id VARCHAR(20) NOT NULL, scenario_name VARCHAR(80) NOT NULL,
  stress_pnl DECIMAL(22,4) NOT NULL, PRIMARY KEY (business_date, portfolio_id, scenario_name),
  CONSTRAINT fk_stress_portfolio FOREIGN KEY (portfolio_id) REFERENCES portfolio(portfolio_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS dq_exception (
  exception_id BIGINT AUTO_INCREMENT PRIMARY KEY, run_id CHAR(36) NOT NULL, business_date DATE NULL,
  dataset_name VARCHAR(50) NOT NULL, severity ENUM('INFO','WARNING','ERROR') NOT NULL,
  rule_code VARCHAR(50) NOT NULL, record_key VARCHAR(255) NULL, message VARCHAR(500) NOT NULL,
  rejected_payload JSON NULL, created_at DATETIME(6) NOT NULL,
  CONSTRAINT fk_dq_run FOREIGN KEY (run_id) REFERENCES pipeline_run(run_id),
  INDEX ix_dq_date_rule (business_date, rule_code)
) ENGINE=InnoDB;

