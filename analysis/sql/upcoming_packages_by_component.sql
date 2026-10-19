-- Packages still to come, with the component the plan names and the
-- paragraphs the asset map puts highest under that component in the
-- project's own documents. This is the join that says which task teams to
-- talk to, and when: a plan's planned date is what the plan records, not
-- evidence of delay.
SELECT p.project_id, p.project_name, p.country_name, p.package_id, p.description_clean,
       p.status, p.planned_date, p.estimated_amount_usd, p.component_number, p.component_name,
       p.in_latest_plan,
       a.best_asset_id, a.probability_supported_or_financed, a.paragraph_id
FROM wbg.upcoming_packages p
LEFT JOIN wbg.asset_map a
       ON p.project_id = ANY (a.project_ids)
      AND a.component_number = p.component_number
ORDER BY p.planned_date NULLS LAST, p.project_id, p.package_id,
         a.probability_supported_or_financed DESC;
