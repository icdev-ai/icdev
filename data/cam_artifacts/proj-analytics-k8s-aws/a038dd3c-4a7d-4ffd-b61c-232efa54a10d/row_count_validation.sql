-- CUI // SP-CTI
-- Row count validation queries — run against both source and target
-- after DMS full-load completes.  Counts must match before enabling CDC.

-- Run on Oracle source:
SELECT table_name, num_rows
FROM   all_tables
WHERE  owner = 'APP_SCHEMA'
ORDER  BY table_name;

-- Run on Aurora target:
SELECT relname AS table_name,
       reltuples::BIGINT AS approximate_row_count
FROM   pg_class
WHERE  relkind = 'r'
  AND  relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
ORDER  BY relname;

-- Spot-check a critical table (adjust table/column names):
-- Oracle:  SELECT COUNT(*), MAX(updated_at) FROM app_schema.orders;
-- Aurora:  SELECT COUNT(*), MAX(updated_at) FROM orders;
