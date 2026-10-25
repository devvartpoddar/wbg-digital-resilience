-- Objects the Metabase dashboards read and nothing reads now (retired
-- 2026-10-25 with Metabase and the database's openrouter() function).
-- Dropped here so a store published before then loses them too; a fresh
-- store never had them. Functions first: they read the views below.
DROP FUNCTION IF EXISTS wbg.try_phrase(text, integer);
DROP FUNCTION IF EXISTS wbg.search_phrase(text, integer);
DROP FUNCTION IF EXISTS wbg.search(text, integer);
DROP FUNCTION IF EXISTS wbg.commitments_under(text, text, text, text, double precision);
DROP FUNCTION IF EXISTS wbg.findings_under(text, double precision, text, text, text, text, text,
                                           double precision, text, text, text, text);
DROP FUNCTION IF EXISTS wbg.findings_under(text, double precision, text, text, text, text, text,
                                           double precision, text, text);
DROP VIEW IF EXISTS wbg.scratch_summary;
DROP VIEW IF EXISTS wbg.scratch_findings;
DROP VIEW IF EXISTS wbg.filter_measures;
DROP VIEW IF EXISTS wbg.filter_types;
DROP VIEW IF EXISTS wbg.filter_regions;
DROP VIEW IF EXISTS wbg.filter_countries;
DROP VIEW IF EXISTS wbg.label_pattern_hits;
DROP VIEW IF EXISTS wbg.measure_asset_matrix;
DROP VIEW IF EXISTS wbg.measure_type_asset_flow;
DROP VIEW IF EXISTS wbg.measure_country_type;
DROP VIEW IF EXISTS wbg.commitment_status_by_quarter;
DROP VIEW IF EXISTS wbg.type_procurement_profile;
DROP VIEW IF EXISTS wbg.measure_finding_lists;
DROP VIEW IF EXISTS wbg.measure_findings_by_project;
