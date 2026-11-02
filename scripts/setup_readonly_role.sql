-- Run ONCE with the admin connection (ADMIN_DATABASE_URL), e.g.:
--   psql "$ADMIN_DATABASE_URL" -v ro_password="'choose-a-long-password'" -v app_password="'another-long-password'" -v schema=dbt_dev -f scripts/setup_readonly_role.sql
-- Layer 2 of the safety design: even if the SQL guard were bypassed, this role cannot write.

CREATE ROLE copilot_ro LOGIN PASSWORD :ro_password;
GRANT USAGE ON SCHEMA :schema TO copilot_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA :schema TO copilot_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA :schema GRANT SELECT ON TABLES TO copilot_ro;
ALTER ROLE copilot_ro SET default_transaction_read_only = on;
ALTER ROLE copilot_ro SET statement_timeout = '10s';
ALTER ROLE copilot_ro SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE copilot_ro CONNECTION LIMIT 10;
-- Make sure nothing sneaks in through PUBLIC.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Separate writable role + schema for the LangGraph checkpointer and the daily request
-- counter (APP_STATE_DATABASE_URL). It cannot read or touch the warehouse marts.
CREATE ROLE copilot_app LOGIN PASSWORD :app_password;
CREATE SCHEMA IF NOT EXISTS app_state AUTHORIZATION copilot_app;
ALTER ROLE copilot_app SET search_path = app_state;
