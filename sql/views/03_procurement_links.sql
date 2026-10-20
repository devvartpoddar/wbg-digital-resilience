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

-- Where each package stands, from the evidence that moves (a notice, a
-- signed contract) before the plan's own status column, which goes stale:
-- a plan can say Pending months after the tender was published. Notices
-- and awards are joined to their package by borrower reference
-- (src/links.py); nothing is linked on a guess.
--
-- package_status, first that applies (wording and precedence: DECIDE):
--   Contract signed   a linked award with a signing date
--   Tendered          a linked notice
--   Overdue           the plan says pending and the planned date is past
--   Planned           the plan says pending and the planned date is today or later
--   Planned, no date  the plan says pending and gives no date
--   Signed per plan   the plan says Signed or Completed, no linked award
--   In process per plan  the plan says Under Implementation
--   Canceled          the plan says Canceled or Terminated
--   Status unknown    anything else
-- Nothing is called complete or delivered: a signed contract is not delivery.
DROP VIEW IF EXISTS wbg.package_status CASCADE;
CREATE VIEW wbg.package_status AS
WITH n AS (
  SELECT DISTINCT ON (package_id) package_id, publication_date AS notice_date,
         deadline_date AS notice_deadline, notice_type
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
       p.estimated_amount_usd, p.planned_date, p.status AS plan_status, p.in_latest_plan,
       p.component_number, p.component_name,
       n.notice_date, n.notice_deadline, a.signed_date, a.supplier, a.contract_amount_usd,
       CASE
         WHEN a.signed_date IS NOT NULL THEN 'Contract signed'
         WHEN n.package_id IS NOT NULL THEN 'Tendered'
         WHEN p.status IN ('Pending', 'Pending Implementation', 'Under Review', 'Planned') THEN
           CASE WHEN p.planned_date IS NULL THEN 'Planned, no date'
                WHEN p.planned_date < current_date THEN 'Overdue'
                ELSE 'Planned' END
         WHEN p.status IN ('Signed', 'Completed') THEN 'Signed per plan'
         WHEN p.status = 'Under Implementation' THEN 'In process per plan'
         WHEN p.status IN ('Canceled', 'Terminated') THEN 'Canceled'
         ELSE 'Status unknown'
       END AS package_status,
       -- The next date a reader can act on: a future planned date or notice deadline.
       (SELECT min(x) FROM unnest(ARRAY[p.planned_date, n.notice_deadline]) x
         WHERE x >= current_date) AS next_date
FROM wbg.packages p
LEFT JOIN n ON n.package_id = p.package_id
LEFT JOIN a ON a.package_id = p.package_id;
COMMENT ON VIEW wbg.package_status IS
  'One row per procurement-plan package with where it stands: Contract signed (a linked award with a signing date), Tendered (a linked notice), Overdue (the plan says pending and its planned date has passed), Planned, Planned no date, Signed per plan, In process per plan, Canceled, Status unknown, in that order. Notices and awards outrank the plan''s status column, which goes stale. A signed contract is not delivery. next_date is the earliest future planned date or notice deadline.';

-- Packages still to come: the plan says pending, no notice or contract has
-- appeared, and the planned date is not past (a past date is Overdue in
-- package_status, not upcoming).
DROP VIEW IF EXISTS wbg.upcoming_packages CASCADE;
CREATE VIEW wbg.upcoming_packages AS
SELECT p.*, s.package_status
FROM wbg.packages_with_project p
JOIN wbg.package_status s USING (package_id)
WHERE s.package_status IN ('Planned', 'Planned, no date');
COMMENT ON VIEW wbg.upcoming_packages IS
  'Packages still to come: the plan says pending (Pending, Pending Implementation, Under Review or Planned), no notice or signed contract is linked, and the planned date is today or later or not given. A pending package whose planned date has passed is Overdue in package_status, and one with a notice is Tendered; neither is listed here. in_latest_plan says whether the package is in the newest plan.';
