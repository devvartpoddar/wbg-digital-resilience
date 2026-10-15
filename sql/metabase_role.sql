-- A read-only login for Metabase on schema wbg. Run once on the box, as the
-- account that runs src/load_pg.py (it owns the wbg tables, and default
-- privileges apply to tables their creator makes). Creating the role needs a
-- database administrator; the password is set by the administrator and kept
-- with Metabase's settings, never in this file:
--   (administrator) --                   ALTER ROLE metabase_reader PASSWORD '...';
--   (pipeline)      psql -d work -f sql/metabase_role.sql
GRANT CONNECT ON DATABASE work TO metabase_reader;
GRANT USAGE ON SCHEMA wbg TO metabase_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA wbg TO metabase_reader;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA wbg TO metabase_reader;
-- Tables are replaced on every load; the role keeps reading the new ones.
ALTER DEFAULT PRIVILEGES IN SCHEMA wbg GRANT SELECT ON TABLES TO metabase_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA wbg GRANT EXECUTE ON FUNCTIONS TO metabase_reader;
