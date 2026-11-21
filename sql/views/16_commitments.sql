-- Commitments: the grain of every dashboard. The commitment rule (src/wbg_portfolio/decide.py
-- commitment_rows, no model call) writes wbg.commitments and wbg.commitment_members at publish:
-- the kept sentences of a project and measure grouped by asset type (investment), by measure type
-- (policy, or an investment sentence with no asset), else by paragraph. The views here add the
-- dashboards' own filters (wbg.dashboard_settings, 13_dashboard_settings.sql), the packages and
-- the evidence.
--
-- Package status precedence for a commitment with several packages:
-- Overdue first, then Planned, Tendered, Contract signed,
-- Contract completed; Under implementation comes after Tendered (the process
-- is under way); a package the newest plan dropped, and Cancelled, last.
DROP VIEW IF EXISTS wbg.package_status_order CASCADE;
CREATE VIEW wbg.package_status_order AS
SELECT * FROM (VALUES (1, 'Overdue'), (2, 'Planned'), (3, 'Tendered'),
                      (4, 'Under implementation'), (5, 'Contract signed'),
                      (6, 'Contract completed'), (7, 'Not in latest plan'), (8, 'Cancelled'))
  AS o(status_rank, package_status);
COMMENT ON VIEW wbg.package_status_order IS
  'The order in which a commitment with several linked packages takes its status: Overdue, Planned, Tendered, Under implementation, Contract signed, Contract completed, Not in latest plan, Cancelled.';

-- One row per kept sentence of a commitment that counts, per project: the base of the views
-- below. The commitment is the rule's (wbg.commitment_members); the dashboards' one filter is added
-- here: Investment Project Financing only (dashboard.ipf_only).
-- Materialised: built once per load, not once per card, and indexed, so a
-- dashboard read is milliseconds.
DO $do$
DECLARE kind "char";
BEGIN
  SELECT c.relkind INTO kind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'wbg' AND c.relname = 'commitment_units';
  IF kind = 'v' THEN EXECUTE 'DROP VIEW wbg.commitment_units CASCADE';
  ELSIF kind = 'm' THEN EXECUTE 'DROP MATERIALIZED VIEW wbg.commitment_units CASCADE';
  END IF;
END
$do$;
CREATE MATERIALIZED VIEW wbg.commitment_units AS
SELECT cm.commitment_id, f.label_set, f.label_id, cm.project_id, f.unit_id, f.unit_kind, f.paragraph_id,
       f.document_id, f.text, f.posterior_probability, f.firmness,
       f.template_project_count, f.copy_unit_ids, f.linked_asset_level, f.component_key,
       f.component_number, f.subcomponent_number,
       CASE coalesce(f.linked_asset_level, 'none')
         WHEN 'unit' THEN 'sentence' WHEN 'paragraph' THEN 'paragraph'
         WHEN 'second_look' THEN 'second_look' ELSE 'none' END AS asset_from,
       c.commitment_key, c.instrument,
       coalesce(nullif(c.asset_id, ''), '(no asset)') AS asset_id, nullif(c.subtype_id, '') AS subtype_id,
       coalesce(nullif(c.type_id, ''), '(no type)') AS type_id,
       -- the wording, for collapsing word-for-word copies
       regexp_replace(lower(f.text), '\s+', ' ', 'g') AS wording
FROM wbg.commitment_members cm
JOIN wbg.commitments c ON c.commitment_id = cm.commitment_id
JOIN wbg.measure_findings f ON f.label_set = cm.label_set AND f.unit_id = cm.unit_id
JOIN wbg.project_info i ON i.project_id = cm.project_id
CROSS JOIN wbg.dashboard_settings s
-- kept: the band rule and digital (measure_findings.is_committed); the rest are in wbg.not_digital_findings
WHERE f.was_sent_to_model AND f.is_committed
  AND (i.is_ipf OR NOT s.ipf_only);
CREATE INDEX ON wbg.commitment_units (commitment_id);
CREATE INDEX ON wbg.commitment_units (label_set, project_id);
CREATE INDEX ON wbg.commitment_units (unit_id);
ANALYZE wbg.commitment_units;
COMMENT ON MATERIALIZED VIEW wbg.commitment_units IS
  'Every kept sentence of a commitment that counts on the dashboards (wbg.commitment_members, in an Investment Project Financing operation: dashboard_settings.ipf_only), once per project, with its commitment''s id and key (commitment_key asset_type, type or paragraph; asset_id and subtype_id, or type_id). asset_from says where the sentence''s asset was found. A sentence with two assets or types belongs to each of those commitments, so a sentence count across commitments counts it more than once.';

-- Each commitment's linked packages with where they stand.
DO $do$
DECLARE kind "char";
BEGIN
  SELECT c.relkind INTO kind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'wbg' AND c.relname = 'commitment_package_status';
  IF kind = 'v' THEN EXECUTE 'DROP VIEW wbg.commitment_package_status CASCADE';
  ELSIF kind = 'm' THEN EXECUTE 'DROP MATERIALIZED VIEW wbg.commitment_package_status CASCADE';
  END IF;
END
$do$;
-- Materialized, and the unit-to-package join fixed first: the planner
-- estimates that join at one row and otherwise rescans package_status for
-- every link (47 s for a count).
CREATE MATERIALIZED VIEW wbg.commitment_package_status AS
WITH l AS MATERIALIZED (
  SELECT u.commitment_id, u.unit_id, cl.package_id, cl.strength, cl.level_rank,
         cl.package_cancelled, cl.package_generic,
         CASE WHEN cl.strength = 'strong' THEN cl.strong_p END AS probability
  FROM wbg.commitment_units u
  JOIN wbg.commitment_package_links cl
    ON cl.label_set = u.label_set AND cl.unit_id = u.unit_id AND cl.project_id = u.project_id
)
SELECT l.commitment_id, l.package_id,
       CASE WHEN bool_or(l.strength = 'strong') THEN 'strong' ELSE 'asset' END AS strength,
       max(l.probability) AS link_probability,
       min(l.level_rank) AS level_rank,
       (ARRAY['strong', 'type_and_component', 'type', 'component', 'asset'])[min(l.level_rank)] AS level,
       bool_or(l.package_cancelled) AS package_cancelled, bool_or(l.package_generic) AS package_generic,
       count(DISTINCT l.unit_id) AS units_linked,
       s.project_id, s.description_clean, s.category, s.method_code, s.estimated_amount_usd,
       s.planned_date, s.due_date, s.plan_status, s.in_latest_plan, s.notice_date,
       s.notice_deadline, s.signed_date, s.supplier, s.package_status, s.next_date
FROM l
JOIN wbg.package_status s ON s.package_id = l.package_id
GROUP BY l.commitment_id, l.package_id, s.project_id, s.description_clean, s.category,
         s.method_code, s.estimated_amount_usd, s.planned_date, s.due_date, s.plan_status,
         s.in_latest_plan, s.notice_date, s.notice_deadline, s.signed_date, s.supplier,
         s.package_status, s.next_date;
CREATE UNIQUE INDEX ON wbg.commitment_package_status (commitment_id, package_id);
CREATE INDEX ON wbg.commitment_package_status (package_id);
ANALYZE wbg.commitment_package_status;
COMMENT ON MATERIALIZED VIEW wbg.commitment_package_status IS
  'One row per commitment and procurement package linked to any of its sentences (commitment_package_links: strong when the package, asked alone, is about the measure; asset when they share a top-level asset): strength is strong when any of the links is, else asset; level and level_rank are the best level among its sentences'' links (1 strong, 2 same asset type and component, 3 same asset type, 4 same asset and component, 5 same asset); link_probability is the strongest strong-link probability and is null when every link is by shared asset; the package''s plan columns, latest notice, signed contract, due_date and package_status (see package_status), as of the last publish (rebuilt there, so a status that turns on today''s date is frozen at that day). A strong link says the package, read alone, is about the measure; an asset link says only that both concern the same kind of asset. Both are the models'' reading, unmeasured until a sample is graded.';

-- Near-identical wording across projects: word-for-word equality is rare,
-- while the operations of one programme print one climate section with
-- small edits. So each committed unit's text
-- is cut into runs of six words (lower case, letters and digits only), and
-- another project counts when its committed units hold at least half of the
-- unit's runs (dashboard_settings.near_copy_share). Materialised: computed
-- once per load, not per card.
DROP MATERIALIZED VIEW IF EXISTS wbg.unit_wording_overlap CASCADE;
CREATE MATERIALIZED VIEW wbg.unit_wording_overlap AS
WITH u AS (
  SELECT DISTINCT f.unit_id, pp.project_id, f.text
  FROM wbg.measure_findings f
  JOIN wbg.paragraph_projects pp ON pp.paragraph_id = f.paragraph_id
  WHERE f.was_sent_to_model AND f.is_committed AND f.is_digital
),
w AS (
  SELECT unit_id, project_id,
         regexp_split_to_array(trim(regexp_replace(lower(text), '[^a-z0-9]+', ' ', 'g')), ' ')
           AS ws
  FROM u
),
sh AS (
  SELECT DISTINCT w.unit_id, w.project_id,
         md5(array_to_string(w.ws[i:i + {{panel:text.shingle_words}} - 1], ' ')) AS h
  FROM w, generate_series(1, greatest(cardinality(w.ws) - {{panel:text.shingle_words}} + 1, 1)) i
),
n AS (SELECT unit_id, project_id, count(*) AS runs FROM sh GROUP BY unit_id, project_id),
shared AS (
  -- another project's committed units, never the same unit listed under a
  -- second project (a regional paragraph is not a copy of itself)
  SELECT a.unit_id, a.project_id, b.project_id AS other_project,
         count(DISTINCT a.h) AS runs_shared
  FROM sh a JOIN sh b ON b.h = a.h AND b.project_id <> a.project_id AND b.unit_id <> a.unit_id
  GROUP BY a.unit_id, a.project_id, b.project_id
)
SELECT n.unit_id, n.project_id, n.runs,
       count(s.other_project) FILTER (WHERE s.runs_shared >= ds.near_copy_share * n.runs)
         AS near_copy_projects
FROM n
CROSS JOIN wbg.dashboard_settings ds
LEFT JOIN shared s ON s.unit_id = n.unit_id AND s.project_id = n.project_id
GROUP BY n.unit_id, n.project_id, n.runs, ds.near_copy_share;
CREATE INDEX ON wbg.unit_wording_overlap (unit_id, project_id);
COMMENT ON MATERIALIZED VIEW wbg.unit_wording_overlap IS
  'For each committed unit and its project: how many other projects'' committed units hold at least half of its six-word runs (near-identical wording, small edits allowed). Rebuilt on every load.';

-- A commitment dropped by a later restructuring: only where a later paper's
-- restructuring table marks the component the sentence sits under "Marked
-- for Deletion" (matched by name). A commitment none of whose sentences sits
-- under a known component is NULL, not false: short papers restate nothing,
-- so absence from a later paper is not read as dropping (on 2026-10-20 that
-- reading showed 38 per cent of commitments as missing).
DROP VIEW IF EXISTS wbg.unit_dropped_by_restructuring CASCADE;
CREATE VIEW wbg.unit_dropped_by_restructuring AS
SELECT u.commitment_id, u.unit_id,
       CASE WHEN coalesce(u.component_key, '') = '' THEN NULL
            ELSE EXISTS (
              SELECT 1 FROM wbg.components c
              JOIN wbg.documents d0 ON d0.document_id = u.document_id
              WHERE u.project_id = ANY (c.project_ids) AND c.read_from = 'restructuring'
                AND c.restructuring_action = 'Marked for Deletion'
                AND c.disclosure_date > d0.disclosure_date
                AND wbg.same_component(c.name_key, u.component_key))
       END AS dropped
FROM wbg.commitment_units u;
COMMENT ON VIEW wbg.unit_dropped_by_restructuring IS
  'For each committed unit: true when a later restructuring paper marks its component "Marked for Deletion", false when its component is known and no later paper does, NULL when the unit sits under no known component.';

-- One row per commitment that counts: the grain every dashboard page reads (the summary of
-- wbg.commitments with the dashboards' filters, the packages and the project). Materialised
-- with commitment_units: built once per load, indexed, so a read from the app
-- returns in milliseconds (a read of the plain view ran for over ten minutes
-- and held the views' locks while it did).
DO $do$
DECLARE kind "char";
BEGIN
  SELECT c.relkind INTO kind FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'wbg' AND c.relname = 'commitment_summary';
  IF kind = 'v' THEN EXECUTE 'DROP VIEW wbg.commitment_summary CASCADE';
  ELSIF kind = 'm' THEN EXECUTE 'DROP MATERIALIZED VIEW wbg.commitment_summary CASCADE';
  END IF;
END
$do$;
CREATE MATERIALIZED VIEW wbg.commitment_summary AS
WITH c AS (
  SELECT u.commitment_id, u.project_id, u.label_set,
         -- the best place any of its sentences' assets came from: the sentence, then the second look,
         -- then the paragraph; none only when no sentence has an asset
         CASE WHEN bool_or(u.asset_from = 'sentence') THEN 'sentence'
              WHEN bool_or(u.asset_from = 'second_look') THEN 'second_look'
              WHEN bool_or(u.asset_from = 'paragraph') THEN 'paragraph'
              ELSE 'none' END AS asset_from,
         string_agg(DISTINCT nullif(coalesce(nullif(u.subcomponent_number, ''),
                                             u.component_number), ''), ', ') AS components,
         count(DISTINCT u.wording) AS sentences,
         count(DISTINCT u.paragraph_id) AS paragraphs,
         count(DISTINCT u.document_id) AS documents,
         max(u.posterior_probability) AS strongest_p,
         -- dropped only if every sentence under a known component was
         bool_and(ud.dropped) FILTER (WHERE ud.dropped IS NOT NULL) AS dropped_by_restructuring,
         min(d.disclosure_date) AS first_seen_date,
         coalesce(max(wo.near_copy_projects), 0) AS same_wording_projects
  FROM wbg.commitment_units u
  LEFT JOIN wbg.documents d ON d.document_id = u.document_id
  LEFT JOIN wbg.unit_wording_overlap wo ON wo.unit_id = u.unit_id AND wo.project_id = u.project_id
  LEFT JOIN wbg.unit_dropped_by_restructuring ud
         ON ud.commitment_id = u.commitment_id AND ud.unit_id = u.unit_id
  GROUP BY u.commitment_id, u.project_id, u.label_set
),
p AS (
  SELECT ps.commitment_id, count(*) AS linked_packages,
         (array_agg(ps.package_status ORDER BY o.status_rank, ps.package_id))[1] AS status,
         min(ps.next_date) AS next_date,
         -- the linked packages' plan estimates, where the plan gives one
         sum(ps.estimated_amount_usd) AS linked_package_estimate_usd,
         count(ps.estimated_amount_usd) AS linked_packages_with_estimate
  FROM wbg.commitment_package_status ps
  LEFT JOIN wbg.package_status_order o ON o.package_status = ps.package_status
  GROUP BY ps.commitment_id
),
-- The cost of the components a commitment's sentences sit under (the
-- project_components figure: all funding sources), only when every one of
-- them has a cost; NULL when no sentence sits under a known component.
cc AS (
  SELECT x.commitment_id,
         CASE WHEN count(*) = count(pc.cost_usd_millions) THEN sum(pc.cost_usd_millions) END
           AS component_cost_usd_m
  FROM (SELECT DISTINCT commitment_id, project_id, component_number
        FROM wbg.commitment_units WHERE coalesce(component_number, '') <> '') x
  LEFT JOIN wbg.project_components pc
         ON pc.project_id = x.project_id AND pc.component_number = x.component_number
        AND pc.component_level = 'component'
  GROUP BY x.commitment_id
)
SELECT c.commitment_id,
       i.project_id, i.project_name, i.cohort_set, i.country_name, i.country_code, i.region,
       i.is_regional,
       c.label_set, l.label_name AS measure_name, l.family,
       k.instrument, k.commitment_key, k.commitment_label, k.type_ids, k.paragraph_id,
       coalesce(nullif(k.asset_id, ''), '(no asset)') AS asset_id,
       coalesce(al.label_name, nullif(k.asset_id, ''), '(no asset)') AS asset_name,
       nullif(k.subtype_id, '') AS subtype_id, st.subtype_name,
       coalesce(nullif(k.type_id, ''), '(no type)') AS type_id,
       coalesce(t.type_name, nullif(k.type_id, ''), '(no type)') AS type_name,
       k.best_link_level, k.best_link_rank, k.strong_packages,
       c.asset_from, c.asset_from AS asset_link_level, c.components,
       i.commitment_usd_millions AS project_commitment_usd_m,
       cc.component_cost_usd_m,
       c.sentences, c.paragraphs, c.documents, c.strongest_p,
       c.first_seen_date, c.same_wording_projects,
       c.dropped_by_restructuring,
       CASE WHEN coalesce(k.asset_id, '') = '' THEN NULL
            ELSE EXISTS (SELECT 1 FROM wbg.project_assets pa
                         WHERE pa.project_id = c.project_id AND pa.asset_id = k.asset_id
                           AND pa.sentences_naming_it > 0) END AS asset_in_asset_map,
       coalesce(p.linked_packages, 0) AS linked_packages,
       p.linked_package_estimate_usd, coalesce(p.linked_packages_with_estimate, 0)
         AS linked_packages_with_estimate,
       coalesce(p.status, 'No package found') AS status,
       p.next_date
FROM c
JOIN wbg.commitments k ON k.commitment_id = c.commitment_id
JOIN wbg.project_info i ON i.project_id = c.project_id
LEFT JOIN p ON p.commitment_id = c.commitment_id
LEFT JOIN cc ON cc.commitment_id = c.commitment_id
LEFT JOIN (SELECT DISTINCT ON (label_set) label_set, label_name, family FROM wbg.labels
           WHERE label_set <> 'assets' ORDER BY label_set, label_id) l ON l.label_set = c.label_set
LEFT JOIN wbg.labels al ON al.label_set = 'assets' AND al.label_id = k.asset_id
LEFT JOIN wbg.asset_subtypes st ON st.asset_id = k.asset_id AND st.subtype_id = k.subtype_id
LEFT JOIN (SELECT DISTINCT ON (label_set, type_id) label_set, type_id, type_name
           FROM wbg.measure_types ORDER BY label_set, type_id, label_id) t
       ON t.label_set = c.label_set AND t.type_id = k.type_id;
CREATE UNIQUE INDEX ON wbg.commitment_summary (commitment_id);
CREATE INDEX ON wbg.commitment_summary (label_set);
ANALYZE wbg.commitment_summary;
COMMENT ON MATERIALIZED VIEW wbg.commitment_summary IS
  'One row per commitment that counts on the dashboards (wbg.commitments, the commitment rule: one project, one measure, one instrument and one key; commitment_key asset_type (asset_id, subtype_id), type (type_id) or paragraph (a bucket of sentences with nothing else to group them by; paragraph_id)), held up by its kept sentences (dashboard_settings says which count). best_link_level is the best level of its package links (strong, type_and_component, type, component, asset). sentences collapses word-for-word copies across documents; strongest_p is the highest posterior probability; first_seen_date the earliest disclosure date; same_wording_projects the most other projects whose committed sentences hold at least half of one of its sentences'' six-word runs (near-identical wording); dropped_by_restructuring is true when a later paper marks the component of every sentence with a known component for deletion, NULL when none has a known component; asset_in_asset_map whether a sentence of the project names this asset in the asset map (wbg.project_assets). status is the first of its linked packages'' statuses in package_status_order, or No package found. A commitment in an appraisal document is design intent, not delivery; nothing here is causal; the pipeline misses real commitments. Three candidate values, none chosen: project_commitment_usd_m is the project''s total commitment from the projects interface (see wbg.projects: its two forms can disagree), repeated on every row; component_cost_usd_m is the cost of the components the sentences sit under, all funding sources, NULL when no sentence sits under a known component or one of them has no cost; linked_package_estimate_usd sums the plan estimates of the linked packages that give one (linked_packages_with_estimate of linked_packages). asset_link_level repeats asset_from: sentence if any sentence names its asset itself, else second_look if the chat model named one for a sentence, else paragraph, else none.';

-- The commitment's sentences, one row per distinct wording (the strongest
-- unit printing it).
DROP VIEW IF EXISTS wbg.commitment_sentences CASCADE;
CREATE VIEW wbg.commitment_sentences AS
SELECT DISTINCT ON (u.commitment_id, u.wording)
       u.commitment_id, u.unit_id, u.text, u.posterior_probability, u.firmness, pa.section_title, d.document_kind, d.disclosure_date AS document_date,
       u.document_id, u.paragraph_id, pa.page_from AS page_number,
       wbg.https(d.pdf_url) || CASE WHEN pa.page_from IS NOT NULL THEN '#page=' || pa.page_from ELSE '' END
         AS pdf_url,
       (SELECT count(DISTINCT split_part(x, ':', 1)) FROM unnest(u.copy_unit_ids) x
         WHERE split_part(x, ':', 1) <> u.document_id) AS also_in_documents
FROM wbg.commitment_units u
LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = u.paragraph_id
LEFT JOIN wbg.documents d ON d.document_id = u.document_id
ORDER BY u.commitment_id, u.wording, u.posterior_probability DESC, u.unit_id;
COMMENT ON VIEW wbg.commitment_sentences IS
  'One row per commitment and distinct sentence (word-for-word copies collapsed to the strongest unit): the text, its posterior probability, the wording''s firmness, its section, document kind and date, and in how many other documents the same words are printed.';

-- The commitment's paragraphs, each with its sentences marked by offset.
DROP VIEW IF EXISTS wbg.commitment_evidence CASCADE;
CREATE VIEW wbg.commitment_evidence AS
WITH m AS (
  SELECT DISTINCT u.commitment_id, u.paragraph_id,
         su.text_start_offset - pa.text_start_offset AS s,
         su.text_end_offset - pa.text_start_offset AS e
  FROM wbg.commitment_units u
  JOIN wbg.search_units su ON su.unit_id = u.unit_id
  JOIN wbg.paragraphs pa ON pa.paragraph_id = u.paragraph_id
  WHERE su.text_start_offset >= pa.text_start_offset AND su.text_end_offset <= pa.text_end_offset
),
seq AS (
  SELECT m.*, coalesce(max(e) OVER (PARTITION BY commitment_id, paragraph_id ORDER BY s, e
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS pe
  FROM m
),
marked AS (
  SELECT q.commitment_id, q.paragraph_id,
         string_agg(substr(pa.text, q.pe + 1, greatest(q.s - q.pe, 0))
                    || '【' || substr(pa.text, greatest(q.s, q.pe) + 1, q.e - greatest(q.s, q.pe))
                    || '】', '' ORDER BY q.s, q.e)
           || substr(pa.text, max(q.e) + 1) AS paragraph_marked
  FROM seq q JOIN wbg.paragraphs pa ON pa.paragraph_id = q.paragraph_id
  WHERE q.e > q.pe
  GROUP BY q.commitment_id, q.paragraph_id, pa.text
),
agg AS (
  SELECT u.commitment_id, u.paragraph_id, count(DISTINCT u.wording) AS sentences,
         max(u.posterior_probability) AS strongest_p,
         (array_agg(u.text ORDER BY u.posterior_probability DESC, u.unit_id))[1] AS key_sentence
  FROM wbg.commitment_units u GROUP BY u.commitment_id, u.paragraph_id
)
SELECT a.commitment_id, a.paragraph_id, pa.document_id, d.document_kind,
       d.disclosure_date AS document_date, pa.section_title,
       coalesce(nullif(pa.subcomponent_name, ''), pa.component_name) AS component_name,
       pa.page_from AS page_number,
       wbg.https(d.pdf_url) || CASE WHEN pa.page_from IS NOT NULL THEN '#page=' || pa.page_from ELSE '' END
         AS pdf_url,
       a.sentences, a.strongest_p, a.key_sentence,
       coalesce(mk.paragraph_marked, pa.text) AS paragraph_marked
FROM agg a
JOIN wbg.paragraphs pa ON pa.paragraph_id = a.paragraph_id
LEFT JOIN wbg.documents d ON d.document_id = pa.document_id
LEFT JOIN marked mk ON mk.commitment_id = a.commitment_id AND mk.paragraph_id = a.paragraph_id;
COMMENT ON VIEW wbg.commitment_evidence IS
  'One row per commitment and paragraph holding its sentences: the document, its kind and date, section, component, page and a PDF link to that page, how many of the commitment''s sentences the paragraph holds, the strongest posterior probability, the key sentence, and the paragraph text with each of the commitment''s sentences wrapped in 【 】 (placed by character offsets, not by search).';
