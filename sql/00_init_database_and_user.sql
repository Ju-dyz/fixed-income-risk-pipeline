-- Run once as a MySQL administrator before the Python pipeline.
CREATE DATABASE IF NOT EXISTS fixed_income_risk_pipeline
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'fi_risk_user'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
CREATE USER IF NOT EXISTS 'fi_risk_user'@'127.0.0.1'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

ALTER USER 'fi_risk_user'@'localhost'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
ALTER USER 'fi_risk_user'@'127.0.0.1'
  IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

GRANT ALL PRIVILEGES ON fixed_income_risk_pipeline.*
  TO 'fi_risk_user'@'localhost';
GRANT ALL PRIVILEGES ON fixed_income_risk_pipeline.*
  TO 'fi_risk_user'@'127.0.0.1';

SHOW DATABASES LIKE 'fixed_income_risk_pipeline';
SELECT User, Host FROM mysql.user WHERE User = 'fi_risk_user';
SHOW GRANTS FOR 'fi_risk_user'@'localhost';
SHOW GRANTS FOR 'fi_risk_user'@'127.0.0.1';
