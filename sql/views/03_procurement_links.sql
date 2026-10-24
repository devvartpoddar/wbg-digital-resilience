-- Notices and awards next to the plan package they are linked to and their project.
DROP VIEW IF EXISTS wbg.notices_with_package;
CREATE VIEW wbg.notices_with_package AS
SELECT n.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount_usd AS package_estimated_amount_usd,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       pr.project_name, pr.country_name AS project_country_name, pr.region
FROM wbg.notices n
LEFT JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = n.project_id;
COMMENT ON VIEW wbg.notices_with_package IS
  'One procurement notice with its linked plan package (where the borrower reference identified one) and its project.';

DROP VIEW IF EXISTS wbg.awards_with_package;
CREATE VIEW wbg.awards_with_package AS
SELECT a.*, p.description_clean AS package_description, p.status AS package_status,
       p.planned_date AS package_planned_date, p.estimated_amount_usd AS package_estimated_amount_usd,
       p.component_number AS package_component_number, p.component_name AS package_component_name,
       pr.project_name, pr.country_name AS project_country_name
FROM wbg.awards a
LEFT JOIN wbg.packages p USING (package_id)
LEFT JOIN wbg.projects pr ON pr.project_id = a.project_id;
COMMENT ON VIEW wbg.awards_with_package IS
  'One signed contract with its linked plan package (where the borrower reference identified one) and its project.';

-- When a package falls due: the plan's revised date where it prints one,
-- else its planned date (most pending packages with a revised date have it later than the planned one
-- packages past their planned date have a revised date still ahead). The
-- parser keeps a row's last printed date as revised_date (section 3.1 step
-- 9), which for a pending package can be a later milestone rather than a
-- new tender date; this function is the one place to change the rule.
DROP FUNCTION IF EXISTS wbg.due_date(date, date) CASCADE;
CREATE FUNCTION wbg.due_date(planned date, revised date) RETURNS date
LANGUAGE sql IMMUTABLE AS $$ SELECT coalesce(revised, planned) $$;

-- The plan statuses that mean a package is still to come (the same list
-- wherever a view asks).
DROP FUNCTION IF EXISTS wbg.is_upcoming(text) CASCADE;
CREATE FUNCTION wbg.is_upcoming(status text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned')
$$;
COMMENT ON FUNCTION wbg.due_date(date, date) IS
  'The date a package falls due: its revised date where the plan prints one, else its planned date.';

-- Where each package stands. The plan's own status first; where the plan
-- still says pending, a linked notice or signed contract (src/links.py, by
-- borrower reference) shows it has moved on:
--   Contract completed    the plan says Completed
--   Contract signed       the plan says Signed, or it says pending and a
--                         linked award has a signing date
--   Under implementation  the plan says Under Implementation
--   Cancelled             the plan says Canceled or Terminated
--   Tendered              pending in the plan, a linked notice
--   Not in latest plan    pending, nothing linked, and the newest plan no
--                         longer lists it (557 of 850 "overdue" packages on
--                         2026-10-20 were such: dropped or replanned, not late)
--   Overdue               pending, nothing linked, in the latest plan, due date past
--   Planned               pending, nothing linked, due today or later or no date
-- A status the plan leaves blank or unknown is read as pending. Nothing is
-- called complete or delivered: a completed contract is not delivery.
DROP VIEW IF EXISTS wbg.package_status CASCADE;
CREATE VIEW wbg.package_status AS
WITH n AS (
  SELECT DISTINCT ON (package_id) package_id, publication_date AS notice_date,
         deadline_date AS notice_deadline
  FROM wbg.notices WHERE package_id IS NOT NULL
  ORDER BY package_id, publication_date DESC NULLS LAST, notice_id DESC
),
a AS (
  SELECT DISTINCT ON (package_id) package_id, signed_date, supplier_name AS supplier,
         total_amount_usd AS contract_amount_usd
  FROM wbg.awards WHERE package_id IS NOT NULL AND signed_date IS NOT NULL
  ORDER BY package_id, signed_date DESC, award_id DESC
)
SELECT p.package_id, p.project_id, p.description_clean, p.category, p.method_code,
       p.estimated_amount_usd, p.planned_date, p.revised_date,
       wbg.due_date(p.planned_date, p.revised_date) AS due_date,
       p.status AS plan_status, p.in_latest_plan, p.component_number, p.component_name,
       n.notice_date, n.notice_deadline, a.signed_date, a.supplier, a.contract_amount_usd,
       CASE
         WHEN p.status = 'Completed' THEN 'Contract completed'
         WHEN p.status = 'Signed' THEN 'Contract signed'
         WHEN p.status = 'Under Implementation' THEN 'Under implementation'
         WHEN p.status IN ('Canceled', 'Terminated') THEN 'Cancelled'
         WHEN a.signed_date IS NOT NULL THEN 'Contract signed'
         WHEN n.package_id IS NOT NULL THEN 'Tendered'
         WHEN NOT coalesce(p.in_latest_plan, true) THEN 'Not in latest plan'
         WHEN wbg.due_date(p.planned_date, p.revised_date) < current_date THEN 'Overdue'
         ELSE 'Planned'
       END AS package_status,
       -- A date to act on, only while the package is still to come: a future
       -- due date for a planned one, a future deadline for a tendered one.
       CASE
         WHEN p.status IN ('Completed', 'Signed', 'Under Implementation', 'Canceled',
                           'Terminated') OR a.signed_date IS NOT NULL THEN NULL
         WHEN n.package_id IS NOT NULL
           THEN CASE WHEN n.notice_deadline >= current_date THEN n.notice_deadline END
         WHEN NOT coalesce(p.in_latest_plan, true) THEN NULL
         WHEN wbg.due_date(p.planned_date, p.revised_date) >= current_date
           THEN wbg.due_date(p.planned_date, p.revised_date)
       END AS next_date
FROM wbg.packages p
LEFT JOIN n ON n.package_id = p.package_id
LEFT JOIN a ON a.package_id = p.package_id;
COMMENT ON VIEW wbg.package_status IS
  'One row per procurement-plan package with where it stands: the plan''s own status first (Contract completed, Contract signed, Under implementation, Cancelled); where the plan says pending, a linked signed contract (Contract signed) or notice (Tendered); else Not in latest plan when the newest plan no longer lists it, Overdue when its due date (the revised date where printed, else the planned date) has passed, Planned otherwise. A package with a linked notice or contract is never Overdue. A completed contract is not delivery. next_date is set only while the package is still to come: a future due date (Planned) or notice deadline (Tendered).';

-- Packages still to come: Planned in package_status (pending, nothing
-- linked, due today or later, or no date).
DROP VIEW IF EXISTS wbg.upcoming_packages CASCADE;
CREATE VIEW wbg.upcoming_packages AS
SELECT p.*, s.package_status, s.due_date
FROM wbg.packages_with_project p
JOIN wbg.package_status s USING (package_id)
WHERE s.package_status = 'Planned';
COMMENT ON VIEW wbg.upcoming_packages IS
  'Packages still to come: the plan says pending (or gives no status), no notice or signed contract is linked, and the due date (the revised date where printed, else the planned date) is today or later or not given. A pending package past its due date is Overdue in package_status, one with a notice Tendered; neither is listed here. in_latest_plan says whether the package is in the newest plan.';
