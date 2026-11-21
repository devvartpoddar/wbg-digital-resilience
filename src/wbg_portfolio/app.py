"""What the Shiny app (repository devvartpoddar/shiny, apps/wbg) reads, and the
little it computes on top. Read only: every function takes `q`, a function
(sql, params) -> pandas.DataFrame (kit.query_df in the app), and reads the
views of schema wbg (sql/views/, docs/method/dashboards.md); nothing here
writes, asks a model or embeds.

The app reads one frame of commitments and filters it in memory (a few
thousand rows), so every chart, board and drill on a page agrees with the
others. The sentences behind a drill are read only for the rows shown.
"""
import pandas as pd

from wbg_portfolio import db

# Where a commitment's procurement stands, from its most pressing linked
# package (wbg.commitment_summary.status, docs/method/dashboards.md "A commitment's
# status"). Package links are found for about a third of commitments, so
# "No package linked" is the common case, not a finding.
DROPPED = "Package dropped"
STAGES = ("Completed", "Under way", "Coming up", DROPPED, "No package linked")
STAGE_OF_STATUS = {
    "Contract completed": "Completed",
    "Under implementation": "Under way", "Contract signed": "Under way",
    "Overdue": "Coming up", "Planned": "Coming up", "Tendered": "Coming up",
    "Not in latest plan": DROPPED, "Cancelled": DROPPED,
    "No package found": "No package linked",
}
# Which stage a sentence behind several commitments shows: the one that most
# needs a reader's attention first, as wbg.package_status_order does for a
# commitment with several packages (a package still to come before one signed).
STAGE_URGENCY = ("Coming up", "Under way", "Completed", DROPPED, "No package linked")
# What a package's status is called where it is listed (the coming-up list).
COMING_UP_STATUSES = ("Overdue", "Planned", "Tendered")
NO_ASSET = "(no asset)"
NO_TYPE = "(no type)"


def _numbers(df):
    """Postgres numeric arrives as Decimal; the app does arithmetic and plots,
    so a column of Decimals becomes float (None becomes NaN)."""
    from decimal import Decimal
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = pd.to_numeric(df[col].map(lambda v: float(v) if isinstance(v, Decimal) else v), errors="coerce")
    return df


def _sql(text):
    """The views live in schema wbg; a test store names its own (db.SCHEMA)."""
    return text.replace("wbg.", db.SCHEMA + ".") if db.SCHEMA != "wbg" else text


def stage(status):
    return STAGE_OF_STATUS.get(status, "No package linked")


def commitments(q, cohort=None):
    """One row per commitment that counts (wbg.commitment_summary: the commitment rule's project x
    measure x instrument x key) with its place, the project's approval year and its procurement
    stage. cohort:
    None for every project, else a cohort_set ('digital', 'digital_contributor').
    family is the measure's family (climate, cloud_data), read by name so a
    store published before families existed still loads (the column is then
    empty and the app shows everything as one family)."""
    df = _numbers(q(_sql("""
        SELECT c.commitment_id, c.project_id, c.project_name, c.cohort_set, c.country_name,
               c.country_code, c.region, c.is_regional, to_jsonb(c)->>'family' AS family,
               c.label_set, c.measure_name,
               c.instrument, c.commitment_key, c.commitment_label,
               c.asset_id, c.asset_name, c.subtype_id, c.subtype_name, c.type_id, c.type_name,
               c.status, c.next_date, c.sentences, c.strongest_p, c.linked_packages,
               c.best_link_level, c.best_link_rank, h.approval_fiscal_year
        FROM wbg.commitment_summary c
        LEFT JOIN wbg.project_header h USING (project_id)
        WHERE %(cohort)s::text IS NULL OR c.cohort_set = %(cohort)s
        ORDER BY c.project_name, c.measure_name, c.commitment_key, c.commitment_label, c.commitment_id"""),
        {"cohort": cohort}))
    if df.empty:
        return df.assign(stage=pd.Series(dtype=str), place=pd.Series(dtype=str))
    df["asset_name"] = df["asset_name"].fillna(NO_ASSET)
    df["type_name"] = df["type_name"].fillna(NO_TYPE)
    df["stage"] = df["status"].map(stage)
    # A regional project has no country: its region stands in for it.
    df["place"] = df["country_name"].where(~df["is_regional"].fillna(False).astype(bool), df["region"])
    return df


def projects(q, cohort=None):
    """One row per project the commitments can come from: a project with at
    least one document read, of the instruments the dashboards count
    (dashboard.ipf_only), whether or not it has a commitment. The base a
    share of projects is taken of. cohort as in commitments()."""
    df = _numbers(q(_sql("""
        SELECT h.project_id, h.project_name, h.cohort_set, h.country_name, h.country_code,
               h.region, h.is_regional, h.approval_fiscal_year
        FROM wbg.project_header h
        JOIN wbg.project_info i USING (project_id)
        CROSS JOIN wbg.dashboard_settings s
        WHERE h.documents_read > 0 AND (i.is_ipf OR NOT s.ipf_only)
          AND (%(cohort)s::text IS NULL OR h.cohort_set = %(cohort)s)
        ORDER BY h.project_name"""), {"cohort": cohort}))
    if df.empty:
        return df.assign(place=pd.Series(dtype=str))
    df["place"] = df["country_name"].where(~df["is_regional"].fillna(False).astype(bool), df["region"])
    return df


def sentences(q, commitment_ids):
    """Every distinct sentence of each commitment asked for, strongest first:
    its text, page, PDF link, probability, firmness and document kind."""
    ids = list(dict.fromkeys(commitment_ids))
    cols = ["commitment_id", "unit_id", "text", "page_number", "pdf_url", "posterior_probability",
            "firmness", "document_kind", "document_date"]
    if not ids:
        return pd.DataFrame(columns=cols)
    return _numbers(q(_sql("""
        SELECT commitment_id, unit_id, text, page_number, pdf_url, posterior_probability,
               firmness, document_kind, document_date
        FROM wbg.commitment_sentences
        WHERE commitment_id = ANY(%(ids)s)
        ORDER BY commitment_id, posterior_probability DESC, length(text), unit_id"""), {"ids": ids}))


def evidence(q, commitment_ids):
    """The strongest sentence of each commitment asked for: its text, page,
    PDF link, probability and document kind."""
    ids = list(dict.fromkeys(commitment_ids))
    if not ids:
        return pd.DataFrame(columns=["commitment_id", "text", "page_number", "pdf_url",
                                     "posterior_probability", "document_kind", "document_date"])
    return _numbers(q(_sql("""
        SELECT DISTINCT ON (commitment_id) commitment_id, text, page_number, pdf_url,
               posterior_probability, document_kind, document_date
        FROM wbg.commitment_sentences
        WHERE commitment_id = ANY(%(ids)s)
        ORDER BY commitment_id, posterior_probability DESC, length(text), unit_id"""), {"ids": ids}))


def measures(q):
    """One row per measure: its definition, counts and the graded sheet's
    precision (graded_sheet says whether it is an evaluation or a training
    draw; a training draw's figure is provisional)."""
    return _numbers(q(_sql("""
        SELECT label_set, measure_name, to_jsonb(m)->>'family' AS family, definition, commitments,
               projects, countries, types, graded_sheet, graded_units, precision
        FROM wbg.measure_summary m ORDER BY commitments DESC NULLS LAST, measure_name"""), None))


def measure_types(q, label_set=None):
    """Each approved type of each measure (or of one), with its definition."""
    return _numbers(q(_sql("""
        SELECT DISTINCT label_set, type_id, type_name, definition
        FROM wbg.measure_types
        WHERE %(set)s::text IS NULL OR label_set = %(set)s
        ORDER BY label_set, type_name"""), {"set": label_set}))


def assets(q):
    """The digital assets (inputs/taxonomy/assets.csv as loaded): id, name, definition."""
    return _numbers(q(_sql("""
        SELECT label_id AS asset_id, label_name AS asset_name, definition
        FROM wbg.labels WHERE label_set = 'assets' AND is_active
        ORDER BY label_name"""), None))


def project(q, project_id):
    """Everything the project view shows beside its commitments: the header
    (one row), the assets its sentences name, its components with cost, the
    documents read, and its linked packages with their stage."""
    p = {"pid": project_id}
    return {
        "header": _numbers(q(_sql("SELECT * FROM wbg.project_header WHERE project_id = %(pid)s"), p)),
        "assets": _numbers(q(_sql("""
            SELECT asset_id, asset_name, sentences_naming_it, sentences_from_paragraph, best_probability
            FROM wbg.project_assets WHERE project_id = %(pid)s
            ORDER BY sentences_naming_it DESC NULLS LAST, asset_name"""), p)),
        "components": _numbers(q(_sql("""
            SELECT component_level, component_number, component_name, cost_usd_millions, cost_covers
            FROM wbg.project_components WHERE project_id = %(pid)s
            ORDER BY component_number"""), p)),
        "documents": q(_sql("""
            SELECT document_id, document_kind, document_date, title, pdf_url
            FROM wbg.project_documents WHERE project_id = %(pid)s ORDER BY document_date"""), p),
        "packages": packages(q, project_id),
    }


def packages(q, project_id):
    """The packages linked to a project's commitments, once each, with the
    date that matters (next date while still to come, else the due date)."""
    df = _numbers(q(_sql("""
        SELECT DISTINCT ON (package_id) package_id, description_clean AS description, category,
               method_code, estimated_amount_usd, package_status, next_date, due_date, signed_date
        FROM wbg.commitment_package_status WHERE project_id = %(pid)s
        ORDER BY package_id, link_probability DESC NULLS LAST"""), {"pid": project_id}))
    if df.empty:
        return df.assign(stage=pd.Series(dtype=str), date=pd.Series(dtype="datetime64[ns]"),
                         date_kind=pd.Series(dtype=str))
    df["stage"] = df["package_status"].map(stage)
    df["date"] = df["next_date"].fillna(df["signed_date"]).fillna(df["due_date"])
    # Which date it is, so a chart that mixes them can say so.
    df["date_kind"] = "planned"
    df.loc[df["next_date"].isna() & df["signed_date"].notna(), "date_kind"] = "signed"
    df.loc[df["next_date"].isna() & df["signed_date"].isna(), "date_kind"] = "was due"
    df.loc[df["date"].isna(), "date_kind"] = None
    return df.sort_values(["date", "package_id"], na_position="last").reset_index(drop=True)


def coming_up(q, cohort=None, family=None):
    """The packages still to come that are linked to a commitment, soonest
    first: one row per package, with its project, the measures of the
    commitments it is linked to, its status (Overdue, Planned or Tendered),
    the date to act on (a future due date or tender deadline; for an overdue
    package the date it was due) and the plan's estimate. cohort as in
    commitments(); family: only the commitments of that family's measures
    (None: every family)."""
    cols = ["package_id", "project_id", "project_name", "country_name", "region", "is_regional",
            "measures", "description", "package_status", "date", "estimated_amount_usd"]
    df = _numbers(q(_sql("""
        SELECT s.package_id, c.project_id, c.project_name, c.country_name, c.region, c.is_regional,
               string_agg(DISTINCT c.measure_name, '; ' ORDER BY c.measure_name) AS measures,
               s.description_clean AS description, s.package_status,
               coalesce(s.next_date, s.due_date) AS date, s.estimated_amount_usd
        FROM wbg.commitment_package_status s
        JOIN wbg.commitment_summary c USING (commitment_id)
        WHERE s.package_status = ANY(%(statuses)s)
          AND (%(cohort)s::text IS NULL OR c.cohort_set = %(cohort)s)
          AND (%(family)s::text IS NULL OR to_jsonb(c)->>'family' = %(family)s)
        GROUP BY s.package_id, c.project_id, c.project_name, c.country_name, c.region, c.is_regional,
                 s.description_clean, s.package_status, s.next_date, s.due_date, s.estimated_amount_usd
        ORDER BY coalesce(s.next_date, s.due_date) NULLS LAST, c.project_name, s.package_id"""),
        {"statuses": list(COMING_UP_STATUSES), "cohort": cohort, "family": family}))
    if df.empty:
        return pd.DataFrame(columns=[*cols, "place"])
    df["place"] = df["country_name"].where(~df["is_regional"].fillna(False).astype(bool), df["region"])
    return df


# ---------------------------------------------------------------- the redesigned dashboard
#
# One row per commitment (the commitment rule's grouping of a project's kept sentences for a
# measure: src/wbg_portfolio/decide.py commitment_rows), per sentence of a commitment, per linked
# package; the download frames and their notes. Each function is one
# query (package() and download_notes() read a few small parts). Filters are keyword
# arguments, each a value or a list of values; a name the function does not know raises
# TypeError, so a misspelt filter never silently shows everything.
#
# What is read: `commitments`, `commitment_members` and the sentences of `measure_findings` they
# group; `unit_assets` (asset, asset type); `commitment_package_links` (with its levels) and
# `package_status` (procurement);
# `labels`, `measure_types`, `asset_subtypes`. Only operations the dashboards count are
# read (dashboard.ipf_only), as everywhere else in this module.

COMMITMENT_FILTERS = ("project", "country", "region", "measure", "type", "asset", "asset_type",
                      "instrument", "key", "commitment_id")
PACKAGE_FILTERS = ("project", "country", "region", "measure", "asset", "status", "strength",
                   "package_id", "type", "asset_type", "instrument")
_PACKAGE_ONLY = ("status", "strength", "package_id")    # no meaning for a commitment or a sentence
SEPARATOR = "; "          # between the values of a column that holds several (ids, names)
PROPOSED = "OTH."         # a proposed type's id and name start with this (decide.OTH)

# The three statements that go with every output (docs/methodology.md).
STATEMENTS = (
    "A commitment in an appraisal document is design intent, not delivery.",
    "Nothing here is causal.",
    "The pipeline misses real commitments and the miss rate is not known.",
)


def _list(value):
    """A filter value as a list of text, or None for no filter."""
    if value is None:
        return None
    values = [value] if isinstance(value, str) else list(value)
    values = [str(v) for v in values]
    return values or None


def _filters(allowed, filters):
    bad = sorted(set(filters) - set(allowed))
    if bad:
        raise TypeError(f"unknown filter {', '.join(bad)}; known: {', '.join(allowed)}")
    return {k: _list(filters.get(k)) for k in allowed}


def _statuses(values):
    """A package filter takes a status ('Planned') or a stage ('Coming up'): both become statuses."""
    if values is None:
        return None
    out = []
    for v in values:
        out += [s for s, g in STAGE_OF_STATUS.items() if g == v] or [v]
    return out


# Everything about a commitment, built once and read by commitment_detail(),
# commitment_sentences_detail() and download(). cm: the commitments in scope; u: their sentences with
# the measure's reading of each; the rest gather by commitment. {{filters}} is _COMMITMENT_WHERE below.
_COMMITMENT_CTES = """
cm AS (
  SELECT c.commitment_id, c.project_id, c.label_set, c.commitment_label, c.sentences AS n_sentences,
         c.paragraphs AS n_paragraphs, c.documents AS n_documents, nullif(c.instrument, '') AS instrument,
         c.commitment_key, nullif(c.asset_id, '') AS key_asset_id, c.best_link_level, c.best_link_rank,
         coalesce(c.type_ids, '{}') AS type_ids
  FROM wbg.commitments c
  JOIN wbg.project_info i ON i.project_id = c.project_id
  CROSS JOIN wbg.dashboard_settings s
  WHERE (i.is_ipf OR NOT s.ipf_only)
    AND (%(project)s::text[] IS NULL OR c.project_id = ANY (%(project)s::text[]))
    AND (%(commitment_id)s::text[] IS NULL OR c.commitment_id = ANY (%(commitment_id)s::text[]))
    AND (%(key)s::text[] IS NULL OR c.commitment_key = ANY (%(key)s::text[]))
    AND (%(cohort)s::text IS NULL OR i.cohort_set = %(cohort)s::text)),
u AS (
  SELECT DISTINCT ON (a.commitment_id, a.unit_id)
         a.commitment_id, a.project_id, a.label_set, a.unit_id, f.component_number,
         f.document_id, pa.component_name
  FROM wbg.commitment_members a
  JOIN cm ON cm.commitment_id = a.commitment_id
  JOIN wbg.measure_findings f ON f.label_set = a.label_set AND f.unit_id = a.unit_id
  LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = f.paragraph_id
  ORDER BY a.commitment_id, a.unit_id, f.posterior_probability DESC NULLS LAST),
fnd AS (
  SELECT u.commitment_id,
         mode() WITHIN GROUP (ORDER BY u.component_number)
           FILTER (WHERE coalesce(u.component_number, '') <> '') AS component_number,
         mode() WITHIN GROUP (ORDER BY u.component_name)
           FILTER (WHERE coalesce(u.component_name, '') <> '') AS paragraph_component_name,
         min(d.disclosure_date) AS first_document_date
  FROM u LEFT JOIN wbg.documents d ON d.document_id = u.document_id
  GROUP BY u.commitment_id),
typ AS (
  SELECT t.commitment_id, array_agg(t.type_id ORDER BY t.type_id) AS type_arr,
         array_agg(coalesce(mt.type_name, t.type_id) ORDER BY t.type_id) AS type_name_arr,
         bool_or(starts_with(t.type_id, 'OTH.')) AS has_proposed_type
  FROM (SELECT DISTINCT cm.commitment_id, cm.label_set, x.type_id
        FROM cm CROSS JOIN LATERAL unnest(cm.type_ids) AS x(type_id)) t
  LEFT JOIN (SELECT DISTINCT ON (label_set, type_id) label_set, type_id, type_name
             FROM wbg.measure_types ORDER BY label_set, type_id, label_id) mt
         ON mt.label_set = t.label_set AND mt.type_id = t.type_id
  GROUP BY t.commitment_id),
ua AS (
  SELECT u.commitment_id, a.unit_id, a.asset_id, nullif(a.subtype_id, '') AS subtype_id
  FROM u JOIN wbg.unit_assets a ON a.unit_id = u.unit_id
  WHERE a.asset_level <> 'none' AND coalesce(a.asset_id, '') <> ''),
ranked AS (
  -- the commitment's own asset (its key) first, then by how many of its sentences name one
  SELECT r.*, row_number() OVER (PARTITION BY r.commitment_id
                                 ORDER BY (r.asset_id = cm.key_asset_id) DESC NULLS LAST, r.n DESC, r.asset_id) AS rk
  FROM (SELECT commitment_id, asset_id, count(DISTINCT unit_id) AS n,
               mode() WITHIN GROUP (ORDER BY subtype_id) FILTER (WHERE subtype_id IS NOT NULL)
                 AS subtype_id
        FROM ua GROUP BY commitment_id, asset_id) r
  JOIN cm ON cm.commitment_id = r.commitment_id),
allx AS (
  SELECT commitment_id, array_agg(DISTINCT asset_id) AS asset_arr, count(DISTINCT asset_id) AS n_assets,
         coalesce(array_agg(DISTINCT subtype_id) FILTER (WHERE subtype_id IS NOT NULL), '{}')
           AS subtype_arr
  FROM ua GROUP BY commitment_id),
prc AS (
  SELECT u.commitment_id, count(DISTINCT cl.package_id) AS linked_packages,
         count(DISTINCT cl.package_id) FILTER (WHERE cl.strength = 'strong') AS strong_packages,
         min(ps.next_date) AS next_package_date,
         array_agg(DISTINCT cl.package_id) AS package_arr
  FROM u
  JOIN wbg.commitment_package_links cl
    ON cl.label_set = u.label_set AND cl.unit_id = u.unit_id AND cl.project_id = u.project_id
  LEFT JOIN wbg.package_status ps ON ps.package_id = cl.package_id
  GROUP BY u.commitment_id),
comp AS (
  SELECT project_id, component_number, min(component_name) AS component_name
  FROM wbg.project_components WHERE component_level = 'component' GROUP BY project_id, component_number),
x AS (
  SELECT cm.commitment_id, cm.project_id, h.project_name, h.country_name, h.country_code, h.region,
         h.is_regional, CASE WHEN h.is_regional THEN h.region ELSE h.country_name END AS place,
         h.approval_fiscal_year, cm.label_set, l.label_name AS measure_name, l.family,
         cm.commitment_label, cm.commitment_key,
         coalesce(typ.type_arr, '{}') AS type_arr, coalesce(typ.type_name_arr, '{}') AS type_name_arr,
         coalesce(typ.has_proposed_type, false) AS has_proposed_type,
         r1.asset_id, coalesce(al1.label_name, r1.asset_id) AS asset_name,
         r1.subtype_id, st1.subtype_name,
         r2.asset_id AS asset2_id, coalesce(al2.label_name, r2.asset_id) AS asset2_name,
         r2.subtype_id AS subtype2_id, st2.subtype_name AS subtype2_name,
         coalesce(allx.n_assets, 0) AS n_assets, coalesce(allx.asset_arr, '{}') AS asset_arr,
         coalesce(allx.subtype_arr, '{}') AS subtype_arr,
         cm.instrument,
         CASE WHEN r1.asset_id IS NOT NULL
                THEN coalesce(al1.label_name, r1.asset_id)
                     || CASE WHEN r2.asset_id IS NOT NULL
                             THEN ' + ' || coalesce(al2.label_name, r2.asset_id) ELSE '' END
              ELSE 'none' END AS asset_column,
         fnd.component_number, coalesce(comp.component_name, fnd.paragraph_component_name) AS component_name,
         cm.n_sentences, cm.n_paragraphs, cm.n_documents, fnd.first_document_date,
         coalesce(prc.linked_packages, 0) AS linked_packages,
         coalesce(prc.strong_packages, 0) AS strong_packages, prc.next_package_date,
         nullif(cm.best_link_level, '') AS best_link_level, cm.best_link_rank,
         coalesce(prc.package_arr, '{}') AS package_arr
  FROM cm
  JOIN wbg.project_header h ON h.project_id = cm.project_id
  LEFT JOIN (SELECT DISTINCT ON (label_set) label_set, label_name, family FROM wbg.labels
             WHERE label_set <> 'assets' ORDER BY label_set, label_id) l ON l.label_set = cm.label_set
  LEFT JOIN fnd ON fnd.commitment_id = cm.commitment_id
  LEFT JOIN typ ON typ.commitment_id = cm.commitment_id
  LEFT JOIN allx ON allx.commitment_id = cm.commitment_id
  LEFT JOIN ranked r1 ON r1.commitment_id = cm.commitment_id AND r1.rk = 1
  LEFT JOIN wbg.labels al1 ON al1.label_set = 'assets' AND al1.label_id = r1.asset_id
  LEFT JOIN wbg.asset_subtypes st1 ON st1.asset_id = r1.asset_id AND st1.subtype_id = r1.subtype_id
  LEFT JOIN ranked r2 ON r2.commitment_id = cm.commitment_id AND r2.rk = 2
  LEFT JOIN wbg.labels al2 ON al2.label_set = 'assets' AND al2.label_id = r2.asset_id
  LEFT JOIN wbg.asset_subtypes st2 ON st2.asset_id = r2.asset_id AND st2.subtype_id = r2.subtype_id
  LEFT JOIN prc ON prc.commitment_id = cm.commitment_id
  LEFT JOIN comp ON comp.project_id = cm.project_id AND comp.component_number = fnd.component_number)"""

_COMMITMENT_WHERE = """
  (%(country)s::text[] IS NULL OR x.country_name = ANY (%(country)s::text[])
     OR x.country_code = ANY (%(country)s::text[]))
  AND (%(region)s::text[] IS NULL OR x.region = ANY (%(region)s::text[]))
  AND (%(measure)s::text[] IS NULL OR x.label_set = ANY (%(measure)s::text[])
       OR x.measure_name = ANY (%(measure)s::text[]))
  AND (%(type)s::text[] IS NULL OR x.type_arr && %(type)s::text[]
       OR ('(no type)' = ANY (%(type)s::text[]) AND cardinality(x.type_arr) = 0))
  AND (%(asset)s::text[] IS NULL OR x.asset_arr && %(asset)s::text[]
       OR ('(no asset)' = ANY (%(asset)s::text[]) AND x.n_assets = 0))
  AND (%(asset_type)s::text[] IS NULL OR x.subtype_arr && %(asset_type)s::text[])
  AND (%(instrument)s::text[] IS NULL OR x.instrument = ANY (%(instrument)s::text[]))
  AND (%(family)s::text IS NULL OR %(family)s::text = ANY (string_to_array(x.family, '|')))"""


def _commitment_params(family, filters, cohort=None):
    return dict(_filters(COMMITMENT_FILTERS, filters), family=family, cohort=cohort)


def families(q, cohort=None):
    """The families of measures that exist (climate, cloud_data, ...) and, under each, its
    measures with counts of projects and commitments. A measure whose family is "a|b" has a row
    under both. One row per family and measure: family, label_set (the measure's id),
    measure_name, definition, measure_projects, measure_commitments (the projects with at least
    one commitment of the measure, and the commitments), family_projects, family_commitments (the
    same, over every measure of the family, each project and commitment counted once),
    family_measures (how many measures the family has). A measure with no family reads
    "(no family)". Largest family first, then by measure name."""
    return _numbers(q(_sql("""
        WITH m AS (
          SELECT DISTINCT ON (label_set) label_set, label_name, definition, family
          FROM wbg.labels WHERE label_set <> 'assets' AND is_active ORDER BY label_set, label_id),
        fam AS (
          SELECT trim(f.family) AS family, m.label_set, m.label_name, m.definition
          FROM m CROSS JOIN LATERAL unnest(string_to_array(
                 CASE WHEN coalesce(m.family, '') = '' THEN '(no family)' ELSE m.family END, '|'))
                 AS f(family)),
        a AS (
          SELECT DISTINCT a.label_set, a.commitment_id, a.project_id
          FROM wbg.commitments a JOIN wbg.project_info i ON i.project_id = a.project_id
          CROSS JOIN wbg.dashboard_settings s WHERE (i.is_ipf OR NOT s.ipf_only)
            AND (%(cohort)s::text IS NULL OR i.cohort_set = %(cohort)s::text))
        SELECT fam.family, fam.label_set, fam.label_name AS measure_name, fam.definition,
               (SELECT count(DISTINCT project_id) FROM a WHERE a.label_set = fam.label_set)
                 AS measure_projects,
               (SELECT count(DISTINCT commitment_id) FROM a WHERE a.label_set = fam.label_set)
                 AS measure_commitments,
               (SELECT count(DISTINCT project_id) FROM a JOIN fam f2 ON f2.label_set = a.label_set
                 WHERE f2.family = fam.family) AS family_projects,
               (SELECT count(DISTINCT commitment_id) FROM a JOIN fam f2 ON f2.label_set = a.label_set
                 WHERE f2.family = fam.family) AS family_commitments,
               count(*) OVER (PARTITION BY fam.family) AS family_measures
        FROM fam
        ORDER BY family_measures DESC, fam.family, fam.label_name"""), {"cohort": cohort}))


_COMMITMENT_COLUMNS = """
        x.commitment_id, x.project_id, x.project_name, x.country_name, x.country_code, x.region,
        x.is_regional, x.place, x.approval_fiscal_year, x.label_set, x.measure_name, x.family,
        x.commitment_label, x.commitment_key,
        array_to_string(x.type_arr, '; ') AS type_ids, array_to_string(x.type_name_arr, '; ') AS type_names,
        cardinality(x.type_arr) AS n_types, x.has_proposed_type,
        x.asset_id, x.asset_name, x.subtype_id, x.subtype_name,
        x.asset2_id, x.asset2_name, x.subtype2_id, x.subtype2_name, x.n_assets,
        x.instrument, x.asset_column,
        x.component_number, x.component_name, x.n_sentences, x.n_paragraphs, x.n_documents,
        x.first_document_date, x.linked_packages, x.strong_packages, x.next_package_date,
        x.best_link_level, x.best_link_rank"""


def commitment_detail(q, family=None, *, cohort=None, **filters):
    """One row per commitment (the commitment rule's grouping of a project's kept sentences for a
    measure: by asset type for an investment, by measure type for a policy or an investment with no
    asset, else by paragraph).

    Columns: commitment_id, project_id, project_name, country_name, country_code, region,
    is_regional, place (the country, or the region for a regional project), approval_fiscal_year,
    label_set (the measure's id), measure_name, family (may hold "a|b"), commitment_label (the asset
    and its type, the measure type, or 'Sentences of one paragraph'), commitment_key ('asset_type',
    'type' or 'paragraph': which key grouped it; 'paragraph' is a bucket, not a finding),
    type_ids / type_names (its tags: every type of its sentences, joined with "; "; empty when none),
    n_types, has_proposed_type (a type is a proposed "OTH." type), asset_id, asset_name,
    subtype_id, subtype_name (its main asset: its key's asset, else the one most of its sentences
    name), asset2_id, asset2_name, subtype2_id, subtype2_name (a second asset of its sentences, if
    any), n_assets (how many its sentences name in all), instrument ('investment', 'policy' or empty
    while not asked), asset_column (the asset's name, "A + B" for two; else 'none': policy is an
    instrument, not an asset), component_number, component_name (the component most of its
    sentences sit under), n_sentences, n_paragraphs, n_documents, first_document_date,
    linked_packages (distinct packages linked to its sentences), strong_packages, next_package_date
    (the soonest next date of a linked package still to come), best_link_level ('strong',
    'type_and_component', 'type', 'component', 'asset' or empty), best_link_rank (1 to 5).

    family: only measures of that family. Filters (a value or a list): project, country (name or
    ISO code), region, measure (id or name), type (id; '(no type)' for none), asset (id;
    '(no asset)' for none), asset_type (an asset type's id), instrument, key, commitment_id."""
    p = _commitment_params(family, filters, cohort)
    return _numbers(q(_sql("WITH" + _COMMITMENT_CTES + "\nSELECT" + _COMMITMENT_COLUMNS + """
        FROM x WHERE""" + _COMMITMENT_WHERE + """
        ORDER BY x.project_name, x.measure_name, x.commitment_key, x.commitment_label, x.commitment_id"""), p))


def commitment_sentences_detail(q, commitment_ids=None, family=None, *, cohort=None, **filters):
    """The sentences of commitments: those of `commitment_ids`, or, with none given, of the
    commitments the filters choose (the same family and filters as commitment_detail()). One row per
    commitment and sentence, in reading order (document date, page).

    Columns: commitment_id, project_id, project_name, country_name, region, place, label_set,
    measure_name, commitment_label, commitment_key, instrument and asset_column (the commitment's,
    as in commitment_detail()), unit_id (the sentence), paragraph_id, text, document_id,
    document_kind, document_title, document_date, page_number, pdf_url (the document with #page=,
    the link sentences() gives), section_title, component_number, component_name,
    sentence_instrument (this sentence's reading), asset_id, asset_name, subtype_id, subtype_name
    (its own best asset), type_ids, type_names (its own, joined with "; "), score_first,
    score_context, second_grade, decided_by ('score', 'second_look'; what the strength rests on),
    posterior_probability, firmness."""
    if commitment_ids is not None:
        filters["commitment_id"] = list(dict.fromkeys(commitment_ids)) or ["-"]   # none asked: no rows
    p = _commitment_params(family, filters, cohort)
    return _numbers(q(_sql("WITH" + _COMMITMENT_CTES + """
        SELECT x.commitment_id, x.project_id, x.project_name, x.country_name, x.region, x.place,
               x.label_set, x.measure_name, x.commitment_label, x.commitment_key, x.instrument,
               x.asset_column,
               f.unit_id, f.paragraph_id, f.text, f.document_id, d.document_kind,
               d.title AS document_title, d.disclosure_date AS document_date,
               pa.page_from AS page_number,
               wbg.https(d.pdf_url) || CASE WHEN pa.page_from IS NOT NULL
                                            THEN '#page=' || pa.page_from ELSE '' END AS pdf_url,
               pa.section_title, f.component_number, pa.component_name,
               f.instrument AS sentence_instrument,
               ua.asset_id, coalesce(al.label_name, ua.asset_id) AS asset_name,
               ua.subtype_id, st.subtype_name,
               coalesce(array_to_string(f.type_ids_above_threshold, '; '), '') AS type_ids,
               coalesce((SELECT string_agg(coalesce(mt.type_name, t.id), '; ' ORDER BY t.id)
                         FROM unnest(f.type_ids_above_threshold) AS t(id)
                         LEFT JOIN wbg.measure_types mt ON mt.label_set = f.label_set
                              AND mt.label_id = f.label_id AND mt.type_id = t.id), '') AS type_names,
               f.score_first, f.score_context, f.second_grade, f.decided_by,
               f.posterior_probability, f.firmness
        FROM x
        JOIN wbg.commitment_members a ON a.commitment_id = x.commitment_id
        JOIN wbg.measure_findings f ON f.label_set = a.label_set AND f.unit_id = a.unit_id
        LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = f.paragraph_id
        LEFT JOIN wbg.documents d ON d.document_id = f.document_id
        LEFT JOIN LATERAL (
          SELECT u.asset_id, nullif(u.subtype_id, '') AS subtype_id FROM wbg.unit_assets u
          WHERE u.unit_id = f.unit_id AND u.asset_level <> 'none' AND coalesce(u.asset_id, '') <> ''
          ORDER BY u.asset_rank LIMIT 1) ua ON true
        LEFT JOIN wbg.labels al ON al.label_set = 'assets' AND al.label_id = ua.asset_id
        LEFT JOIN wbg.asset_subtypes st ON st.asset_id = ua.asset_id AND st.subtype_id = ua.subtype_id
        WHERE""" + _COMMITMENT_WHERE + """
        ORDER BY x.project_name, x.measure_name, x.commitment_key, x.commitment_label, x.commitment_id,
                 d.disclosure_date, pa.page_from, f.unit_id"""), p))


_SERVES = """
          AND (EXISTS (
                 SELECT 1 FROM x WHERE x.commitment_id = ANY (ac.commitment_arr)
                   AND (%(type)s::text[] IS NULL OR x.type_arr && %(type)s::text[]
                        OR ('(no type)' = ANY (%(type)s::text[]) AND cardinality(x.type_arr) = 0))
                   AND (%(asset_type)s::text[] IS NULL OR x.subtype_arr && %(asset_type)s::text[])
                   AND (%(instrument)s::text[] IS NULL OR x.instrument = ANY (%(instrument)s::text[]))))
"""


def _packages(q, family, linked_only, filters, cohort=None):
    p = _filters(PACKAGE_FILTERS, filters)
    p["cohort"] = cohort
    p["status"] = _statuses(p["status"])
    p["family"] = family
    p["commitment_id"] = p["key"] = None
    # type, asset_type and instrument are of the commitments a package serves: it counts when it
    # serves one that meets them all (the commitments' own reading, built only when asked for)
    by_commitment = any(p[k] for k in ("type", "asset_type", "instrument"))
    df = _numbers(q(_sql("WITH" + (_COMMITMENT_CTES + ",\n" if by_commitment else "") + """
        ml AS (
          SELECT DISTINCT ON (label_set) label_set, label_name, family FROM wbg.labels
          WHERE label_set <> 'assets' ORDER BY label_set, label_id),
        lk AS (
          SELECT cl.package_id, bool_or(cl.strength = 'strong') AS has_strong,
                 min(cl.level_rank) AS best_link_rank, bool_or(cl.package_cancelled) AS cancelled,
                 bool_or(cl.package_generic) AS generic,
                 max(cl.strong_p) FILTER (WHERE cl.strength = 'strong') AS strong_p,
                 count(DISTINCT cl.unit_id) AS n_sentences,
                 array_agg(DISTINCT cl.label_set) AS label_sets,
                 coalesce(array_agg(DISTINCT cl.label_set) FILTER (WHERE cl.strength = 'strong'), '{}')
                   AS strong_label_sets,
                 coalesce(array_agg(DISTINCT sa.a) FILTER (WHERE coalesce(sa.a, '') <> ''), '{}')
                   AS shared_arr
          FROM wbg.commitment_package_links cl
          LEFT JOIN LATERAL unnest(cl.shared_asset_ids) AS sa(a) ON true
          GROUP BY cl.package_id),
        ac AS (
          SELECT y.package_id, array_agg(y.commitment_id ORDER BY y.commitment_id) AS commitment_arr,
                 array_agg(y.commitment_label ORDER BY y.commitment_id) AS label_arr
          FROM (SELECT DISTINCT cl.package_id, a.commitment_id, c.commitment_label
                FROM wbg.commitment_package_links cl
                JOIN wbg.commitment_members a ON a.project_id = cl.project_id AND a.label_set = cl.label_set
                                             AND a.unit_id = cl.unit_id
                JOIN wbg.commitments c ON c.commitment_id = a.commitment_id) y
          GROUP BY y.package_id),
        pas AS (
          SELECT pa.package_id,
                 array_agg(pa.label_id ORDER BY coalesce(pa.posterior_probability, pa.probability) DESC,
                           pa.label_id) AS asset_arr,
                 array_agg(coalesce(l.label_name, pa.label_id)
                           ORDER BY coalesce(pa.posterior_probability, pa.probability) DESC, pa.label_id)
                   AS asset_name_arr
                 , coalesce(array_agg(pa.subtype_id) FILTER (WHERE coalesce(pa.subtype_id, '') <> ''), '{}')
                   AS subtype_arr
          FROM wbg.package_assets pa
          LEFT JOIN wbg.labels l ON l.label_set = 'assets' AND l.label_id = pa.label_id
          WHERE pa.has_asset
          GROUP BY pa.package_id)
        SELECT ps.package_id, pk.borrower_reference AS package_number, ps.project_id, h.project_name,
               h.country_name, h.country_code, h.region, h.is_regional,
               CASE WHEN h.is_regional THEN h.region ELSE h.country_name END AS place,
               ps.description_clean AS description, ps.component_number, ps.component_name,
               ps.category, ps.method_code, pk.method_name, pk.market_approach,
               ps.estimated_amount_usd, pk.actual_amount_usd, ps.contract_amount_usd,
               ps.planned_date, ps.revised_date, ps.due_date, ps.notice_date, ps.notice_deadline,
               ps.signed_date, ps.supplier, aw.supplier_country,
               pk.plan_document_id, pk.plan_disclosure_date, ps.in_latest_plan, ps.plan_status,
               ps.package_status, ps.next_date,
               CASE WHEN lk.has_strong THEN 'strong' WHEN lk.package_id IS NOT NULL THEN 'asset' END
                 AS link_strength,
               (ARRAY['strong', 'type_and_component', 'type', 'component', 'asset'])[lk.best_link_rank]
                 AS link_level, lk.best_link_rank AS link_rank,
               coalesce(lk.cancelled, false) AS is_cancelled, coalesce(lk.generic, false) AS is_generic,
               lk.strong_p, coalesce(lk.n_sentences, 0) AS n_sentences_linked,
               coalesce(cardinality(ac.commitment_arr), 0) AS n_commitments,
               coalesce(array_to_string(ac.commitment_arr, '; '), '') AS commitment_ids,
               coalesce(array_to_string(ac.label_arr, '; '), '') AS commitment_labels,
               coalesce(array_to_string(lk.strong_label_sets, '; '), '') AS measure_ids,
               coalesce((SELECT string_agg(ml.label_name, '; ' ORDER BY ml.label_name) FROM ml
                          WHERE ml.label_set = ANY (lk.strong_label_sets)), '') AS measure_names,
               pas.asset_arr[1] AS asset_id, pas.asset_name_arr[1] AS asset_name,
               coalesce(array_to_string(pas.asset_arr, '; '), '') AS asset_ids,
               coalesce(array_to_string(pas.asset_name_arr, '; '), '') AS asset_names,
               coalesce(array_to_string(pas.subtype_arr, '; '), '') AS asset_type_ids,
               coalesce(array_to_string(lk.shared_arr, '; '), '') AS shared_asset_ids
        FROM wbg.package_status ps
        JOIN wbg.packages pk ON pk.package_id = ps.package_id
        JOIN wbg.project_info i ON i.project_id = ps.project_id
        JOIN wbg.project_header h ON h.project_id = ps.project_id
        CROSS JOIN wbg.dashboard_settings s
        """ + ("JOIN" if linked_only else "LEFT JOIN") + """ lk ON lk.package_id = ps.package_id
        LEFT JOIN ac ON ac.package_id = ps.package_id
        LEFT JOIN pas ON pas.package_id = ps.package_id
        LEFT JOIN LATERAL (
          SELECT a.supplier_country FROM wbg.awards a
          WHERE a.package_id = ps.package_id AND a.signed_date IS NOT NULL
          ORDER BY a.signed_date DESC, a.award_id DESC LIMIT 1) aw ON true
        WHERE (i.is_ipf OR NOT s.ipf_only)
          AND (%(cohort)s::text IS NULL OR i.cohort_set = %(cohort)s::text)
          AND (%(project)s::text[] IS NULL OR ps.project_id = ANY (%(project)s::text[]))
          AND (%(package_id)s::text[] IS NULL OR ps.package_id = ANY (%(package_id)s::text[]))
          AND (%(country)s::text[] IS NULL OR h.country_name = ANY (%(country)s::text[])
               OR h.country_code = ANY (%(country)s::text[]))
          AND (%(region)s::text[] IS NULL OR h.region = ANY (%(region)s::text[]))
          AND (%(measure)s::text[] IS NULL OR lk.label_sets && %(measure)s::text[])
          AND (%(asset)s::text[] IS NULL OR pas.asset_arr && %(asset)s::text[]
               OR lk.shared_arr && %(asset)s::text[])
          AND (%(status)s::text[] IS NULL OR ps.package_status = ANY (%(status)s::text[]))
          AND (%(strength)s::text[] IS NULL OR
               (CASE WHEN lk.has_strong THEN 'strong' WHEN lk.package_id IS NOT NULL THEN 'asset' END)
                 = ANY (%(strength)s::text[]))
          AND (%(family)s::text IS NULL OR EXISTS (
                 SELECT 1 FROM ml WHERE ml.label_set = ANY (lk.label_sets)
                   AND %(family)s::text = ANY (string_to_array(ml.family, '|'))))
""" + (_SERVES if by_commitment else "") + """
        ORDER BY lk.best_link_rank NULLS LAST, coalesce(ps.next_date, ps.due_date) NULLS LAST, h.project_name,
                 ps.package_id"""),
        p))
    df["stage"] = df["package_status"].map(stage)
    return df


def packages_detail(q, family=None, *, cohort=None, **filters):
    """One row per package linked to a commitment, with every procurement field the published
    tables hold, its project, its asset(s), its link, the measures it names and the commitments
    it serves; by its best link level, then soonest first (next date, else due date).

    Columns: package_id, package_number (the borrower's reference), project_id, project_name,
    country_name, country_code, region, is_regional, place, description, component_number,
    component_name, category, method_code, method_name, market_approach, estimated_amount_usd,
    actual_amount_usd, contract_amount_usd, planned_date, revised_date, due_date (the revised
    date where the plan prints one, else the planned), notice_date, notice_deadline, signed_date,
    supplier, supplier_country, plan_document_id, plan_disclosure_date, in_latest_plan,
    plan_status (the plan's own word), package_status (Planned, Overdue, Tendered, ...), next_date
    (set only while the package is still to come), link_strength ('strong': the package, asked
    alone, is about a measure; 'asset': only a shared asset; strong if any link is strong),
    link_level, link_rank (the best level of its links: strong 1, type_and_component 2, type 3,
    component 4, asset 5), is_cancelled (its plan status says cancelled), is_generic (its
    description is about running the project: project management, audit, vehicles and the like),
    strong_p (best strong-link probability), n_sentences_linked, n_commitments, commitment_ids,
    commitment_labels (joined with "; ", in the same order), measure_ids, measure_names (the
    measures its strong links name, "; " joined), asset_id, asset_name (an asset the package has,
    best first), asset_ids, asset_names ("; " joined, every asset it has), asset_type_ids (their
    types), shared_asset_ids (assets it shares with a linked sentence), stage (as packages()).

    family: only packages linked to a measure of that family. Filters (a value or a list):
    project, country (name or ISO code), region, measure (id; any link), asset (id; the package's
    or a shared one), status (a package status or a stage such as 'Coming up'), strength
    ('strong', 'asset'), package_id, and, of the commitments it serves (it counts when one meets all
    that are given), type, asset_type, instrument."""
    return _packages(q, family, True, filters, cohort)


def package(q, package_id):
    """Everything about one package: {"package": one row (the columns of packages_detail, a
    package with no link included, its link columns then empty), "notices": its notices
    (notice_id, notice_type, notice_status, publication_date, deadline_date, description,
    category, method_code, method_name, country_name, package_link_kind), "awards": its signed
    contracts (award_id, signed_date, no_objection_date, supplier_name, supplier_country,
    total_amount_usd, supplier_amount_usd, currency, review_type, description, category,
    method_code, method_name, package_link_kind), "commitments": the commitments it serves, as
    commitment_detail() gives them}. One query for each part."""
    p = {"pid": package_id}
    pkg = _packages(q, None, False, {"package_id": [package_id]})
    ids = [a for a in pkg["commitment_ids"].iloc[0].split(SEPARATOR) if a] if len(pkg) else []
    return {
        "package": pkg,
        "notices": q(_sql("""
            SELECT notice_id, notice_type, notice_status, publication_date, deadline_date,
                   description_clean AS description, category, method_code, method_name, country_name,
                   package_link_kind
            FROM wbg.notices WHERE package_id = %(pid)s
            ORDER BY publication_date NULLS LAST, notice_id"""), p),
        "awards": _numbers(q(_sql("""
            SELECT award_id, signed_date, no_objection_date, supplier_name, supplier_country,
                   total_amount_usd, supplier_amount_usd, currency, review_type,
                   description_clean AS description, category, method_code, method_name,
                   package_link_kind
            FROM wbg.awards WHERE package_id = %(pid)s
            ORDER BY signed_date NULLS LAST, award_id"""), p)),
        "commitments": commitment_detail(q, commitment_id=ids or "-"),
    }


def not_digital(q, project_id=None, measure=None, *, cohort=None):
    """The sentences that pass the measure rule but that the digital question says are not about a
    digital technology: never kept, kept out of every commitment, listed so a person can see what
    the rule leaves out.
    One row per sentence, measure and project; the models' reading, not a person's.

    Columns: label_set, measure_name, project_id, project_name, country_name, region,
    document_id, document_kind, document_title, document_date, page_number, pdf_url,
    section_title, component_number, unit_id, text, posterior_probability, score_first,
    score_context, second_grade, decided_by, digital_p (the digital question's probability),
    instrument. project_id and measure (an id or a name) are each a value, a list or None."""
    return _numbers(q(_sql("""
        SELECT n.label_set, n.measure_name, n.project_id, n.project_name, n.country_name, h.region,
               n.document_id, d.document_kind, d.title AS document_title,
               d.disclosure_date AS document_date, pa.page_from AS page_number,
               wbg.https(d.pdf_url) || CASE WHEN pa.page_from IS NOT NULL
                                            THEN '#page=' || pa.page_from ELSE '' END AS pdf_url,
               n.section_title, n.component_number, n.unit_id, n.text, n.posterior_probability,
               n.score_first, n.score_context, n.second_grade, n.decided_by, n.digital_p, n.instrument
        FROM wbg.not_digital_findings n
        JOIN wbg.project_info i ON i.project_id = n.project_id
        JOIN wbg.project_header h ON h.project_id = n.project_id
        CROSS JOIN wbg.dashboard_settings s
        LEFT JOIN wbg.search_units su ON su.unit_id = n.unit_id
        LEFT JOIN wbg.paragraphs pa ON pa.paragraph_id = su.paragraph_id
        LEFT JOIN wbg.documents d ON d.document_id = n.document_id
        WHERE (i.is_ipf OR NOT s.ipf_only)
          AND (%(cohort)s::text IS NULL OR i.cohort_set = %(cohort)s::text)
          AND (%(project)s::text[] IS NULL OR n.project_id = ANY (%(project)s::text[]))
          AND (%(measure)s::text[] IS NULL OR n.label_set = ANY (%(measure)s::text[])
               OR n.measure_name = ANY (%(measure)s::text[]))
        ORDER BY n.project_name, n.measure_name, d.disclosure_date, pa.page_from, n.unit_id"""),
        {"project": _list(project_id), "measure": _list(measure), "cohort": cohort}))


def asset_types(q):
    """The assets and their types, names and definitions, for a hover and a
    table. One row per asset and asset type (an asset with none has one row, the type columns
    empty): asset_id, asset_name, asset_definition, subtype_id, subtype_name,
    subtype_definition, subtype_is_active."""
    return _numbers(q(_sql("""
        SELECT a.label_id AS asset_id, a.label_name AS asset_name, a.definition AS asset_definition,
               st.subtype_id, st.subtype_name, st.definition AS subtype_definition,
               st.is_active AS subtype_is_active
        FROM wbg.labels a LEFT JOIN wbg.asset_subtypes st ON st.asset_id = a.label_id
        WHERE a.label_set = 'assets' AND a.is_active
        ORDER BY a.label_name, st.subtype_name"""), None))


def type_definitions(q):
    """Every measure's types with names and definitions, the proposed ones too: label_set,
    measure_name, type_id, type_name, definition, is_proposed (a type proposed from sentences no
    approved type reaches: its id and name start with "OTH."), merged_from."""
    return _numbers(q(_sql("""
        SELECT t.label_set, l.label_name AS measure_name, t.type_id, t.type_name, t.definition,
               starts_with(t.type_id, 'OTH.') AS is_proposed, t.merged_from
        FROM wbg.measure_types t
        LEFT JOIN (SELECT DISTINCT ON (label_set) label_set, label_name FROM wbg.labels
                   WHERE label_set <> 'assets' ORDER BY label_set, label_id) l ON l.label_set = t.label_set
        ORDER BY t.label_set, starts_with(t.type_id, 'OTH.'), t.type_name"""), None))


# ---------------------------------------------------------------- downloads

# Each level's columns, fixed whatever the filters: (column, where it comes from in the frame the
# level is made of, what it means). project_flags and project are made by one query of their own.
_COMMITMENT_DOWNLOAD = [
    ("commitment_id", "commitment_id", "The commitment's id (stable between publishes)."),
    ("project_id", "project_id", "The World Bank project's id."),
    ("project_name", "project_name", "The project's name."),
    ("country_name", "country_name", "The project's country ('(record pending)' where the record is missing)."),
    ("region", "region", "The project's World Bank region."),
    ("approval_fiscal_year", "approval_fiscal_year", "World Bank fiscal year of Board approval."),
    ("family", "family", "The measure's family (several are joined with |)."),
    ("measure_id", "label_set", "The measure's id."),
    ("measure_name", "measure_name", "The measure's name."),
    ("commitment", "commitment_label", "What the commitment is grouped by: the asset and its type, the measure type, or 'Sentences of one paragraph'."),
    ("grouped_by", "commitment_key", "asset_type (an investment: one commitment per asset type), type (a policy, or an investment with no asset: one per measure type) or paragraph (sentences with nothing else to group them by; a bucket, not a finding)."),
    ("instrument", "instrument", "investment (the project builds, buys or runs something) or policy (a law, strategy, standard or plan); the model's reading of the commitment's sentences."),
    ("asset", "asset_column", "The asset when there is one (two joined with +); else 'none'. Policy is not an asset: see instrument."),
    ("asset_id", "asset_id", "The main asset's id: the commitment's own asset, else the one most of its sentences name."),
    ("asset_name", "asset_name", "The main asset's name."),
    ("asset_type_id", "subtype_id", "The main asset's type, id."),
    ("asset_type_name", "subtype_name", "The main asset's type, name."),
    ("second_asset_name", "asset2_name", "A second asset its sentences name, if any."),
    ("second_asset_type_name", "subtype2_name", "The second asset's type."),
    ("type_ids", "type_ids", "The types of the commitment's sentences (ids, joined with ';'); empty when none. An id starting OTH. is a type proposed from sentences no approved type reaches."),
    ("type_names", "type_names", "The same types by name."),
    ("has_proposed_type", "has_proposed_type", "True when one of the types is a proposed OTH. type."),
    ("component_number", "component_number", "The project component its sentences sit under, as the document numbers it."),
    ("component_name", "component_name", "That component's name."),
    ("sentences", "n_sentences", "How many sentences the commitment has."),
    ("paragraphs", "n_paragraphs", "How many paragraphs they sit in."),
    ("documents", "n_documents", "How many documents they come from."),
    ("first_document_date", "first_document_date", "Disclosure date of the earliest document with one of its sentences."),
    ("packages_linked", "linked_packages", "Procurement packages of the project linked to its sentences (at any level)."),
    ("packages_linked_strongly", "strong_packages", "Of those, packages whose own description is about the measure."),
    ("next_package_date", "next_package_date", "The soonest date to act on among linked packages still to come."),
    ("best_link_level", "best_link_level", "The best level among its links: strong (a package names the measure), type_and_component, type, component (the same asset and component), asset (the same asset only). Only strong says the package is about the measure."),
]
_SENTENCE_DOWNLOAD = [
    ("commitment_id", "commitment_id", "The commitment the sentence belongs to."),
    ("project_id", "project_id", "The World Bank project's id."),
    ("project_name", "project_name", "The project's name."),
    ("country_name", "country_name", "The project's country."),
    ("region", "region", "The project's World Bank region."),
    ("measure_id", "label_set", "The measure's id."),
    ("measure_name", "measure_name", "The measure's name."),
    ("commitment", "commitment_label", "What the commitment is grouped by."),
    ("grouped_by", "commitment_key", "asset_type, type or paragraph: see the commitment level."),
    ("instrument", "instrument", "The commitment's instrument (investment or policy)."),
    ("asset", "asset_column", "The commitment's asset: the asset, else 'none'. Policy is not an asset: see instrument."),
    ("sentence_id", "unit_id", "The sentence's id."),
    ("sentence", "text", "The sentence as the document prints it (with its list lead-in where it is an item)."),
    ("document_kind", "document_kind", "pad, additional_financing, restructuring or project_paper_other."),
    ("document_date", "document_date", "The document's disclosure date."),
    ("page", "page_number", "The PDF page the sentence starts on."),
    ("page_link", "pdf_url", "Link to the document at that page."),
    ("section", "section_title", "The section the sentence sits in."),
    ("component_number", "component_number", "The component it sits under."),
    ("component_name", "component_name", "That component's name."),
    ("asset_name", "asset_name", "The sentence's own best asset."),
    ("asset_type_name", "subtype_name", "That asset's type."),
    ("type_ids", "type_ids", "The sentence's types (ids, joined with ';')."),
    ("type_names", "type_names", "The sentence's types by name."),
    ("score_first", "score_first", "The decision model's probability that the sentence commits, asked alone."),
    ("score_context", "score_context", "The same with the paragraph beside the sentence; empty unless asked."),
    ("second_grade", "second_grade", "The chat model's grade 0 to 3; empty unless asked."),
    ("decided_by", "decided_by", "score (the first score alone) or second_look (a middling score confirmed by the two second looks)."),
    ("probability", "posterior_probability", "The probability every decision reads."),
]
_PACKAGE_DOWNLOAD = [
    ("package_id", "package_id", "The package's id."),
    ("package_number", "package_number", "The borrower's reference number for the package."),
    ("project_id", "project_id", "The World Bank project's id."),
    ("project_name", "project_name", "The project's name."),
    ("country_name", "country_name", "The project's country."),
    ("region", "region", "The project's World Bank region."),
    ("description", "description", "The plan's description, cleaned."),
    ("component_number", "component_number", "The component the plan names."),
    ("component_name", "component_name", "That component's name."),
    ("category", "category", "goods, works, consultant_services, non_consulting_services or unknown."),
    ("method_code", "method_code", "The procurement method code."),
    ("method_name", "method_name", "The procurement method in words."),
    ("market_approach", "market_approach", "Open - National, Limited - International, Direct, as printed."),
    ("estimated_amount_usd", "estimated_amount_usd", "The plan's estimate, US$."),
    ("actual_amount_usd", "actual_amount_usd", "The plan's actual amount, US$."),
    ("contract_amount_usd", "contract_amount_usd", "The signed contract's total, US$."),
    ("planned_date", "planned_date", "The planned date the plan records."),
    ("revised_date", "revised_date", "The revised date the plan records."),
    ("notice_date", "notice_date", "Publication date of the latest linked notice."),
    ("notice_deadline", "notice_deadline", "That notice's bid deadline."),
    ("signed_date", "signed_date", "Date the contract was signed."),
    ("supplier", "supplier", "The supplier of the signed contract."),
    ("supplier_country", "supplier_country", "The supplier's country."),
    ("plan_document_id", "plan_document_id", "The newest plan version that lists the package."),
    ("plan_disclosure_date", "plan_disclosure_date", "That plan's disclosure date."),
    ("package_status", "package_status", "Where the package stands (Planned, Overdue, Tendered, Contract signed, ...). A past date is what the plan records, not evidence of delay."),
    ("next_date", "next_date", "The date to act on, set only while the package is still to come."),
    ("link_strength", "link_strength", "strong (the package, asked alone, is about a measure) or asset (only a shared asset)."),
    ("link_level", "link_level", "The best level of its links: strong, type_and_component, type, component or asset (same asset only)."),
    ("cancelled", "is_cancelled", "True when the plan status says the package was cancelled."),
    ("generic", "is_generic", "True when the description is about running the project (project management, audit, vehicles, office furniture, workshops, per diem, accounting, salaries)."),
    ("measures_named", "measure_names", "The measures its strong links name (joined with ';')."),
    ("asset_names", "asset_names", "The assets the package is for, best first (joined with ';')."),
    ("asset_type_ids", "asset_type_ids", "Their types (joined with ';')."),
    ("commitments", "commitment_labels", "The commitments it serves (joined with ';')."),
    ("commitments_served", "n_commitments", "How many commitments it serves."),
]
_PROJECT_FACTS = [
    ("project_id", "The World Bank project's id."),
    ("project_name", "The project's name."),
    ("country_name", "The project's country."),
    ("country_code", "ISO 3166 country code (empty for a regional project)."),
    ("region", "The project's World Bank region."),
    ("approval_fiscal_year", "World Bank fiscal year of Board approval."),
]
_PROJECT_COLUMNS = _PROJECT_FACTS + [
    ("status", "The project's status in the projects interface."),
    ("closing_date", "The project's closing date."),
    ("commitment_usd_millions", "Total Bank commitment, US$ millions, from the projects interface."),
    ("practice", "The Global Practice leading the project."),
    ("documents_read", "Appraisal documents read for the project."),
    ("measures", "Measures with at least one commitment in the project (within the family and filters)."),
    ("commitments", "Commitments (within the family and filters)."),
    ("investment", "Of those, commitments whose instrument is investment."),
    ("policy", "Of those, commitments whose instrument is policy."),
    ("assets", "Distinct assets its commitments name."),
    ("packages_linked", "Distinct procurement packages linked to those commitments."),
    ("next_package_date", "The soonest date to act on among those packages still to come."),
]
DOWNLOAD_LEVELS = ("project_flags", "project", "commitment", "sentence", "package")
_DOWNLOAD_SPEC = {"commitment": _COMMITMENT_DOWNLOAD, "sentence": _SENTENCE_DOWNLOAD,
                  "package": _PACKAGE_DOWNLOAD}
_DATE_COLUMNS = ("closing_date", "next_package_date")


def _project_frame(q, family, filters, cohort=None):
    """Projects with their facts, counts and the measures they have: one query for the levels
    project and project_flags. A filter on measure, type, asset, asset type, instrument or key keeps
    the projects with a commitment that meets it, and the counts are of those commitments."""
    p = _commitment_params(family, filters, cohort)
    p["restrict"] = any(p[k] for k in ("measure", "type", "asset", "asset_type", "instrument", "key",
                                       "commitment_id"))
    df = _numbers(q(_sql("WITH" + _COMMITMENT_CTES + """,
        sel AS (SELECT x.* FROM x WHERE""" + _COMMITMENT_WHERE + """),
        fm AS (SELECT array_agg(label_set ORDER BY label_set) AS ids FROM (
                 SELECT DISTINCT label_set FROM wbg.labels
                 WHERE label_set <> 'assets' AND is_active
                   AND (%(family)s::text IS NULL OR %(family)s::text = ANY (string_to_array(family, '|')))
               ) m),
        b AS (
          SELECT h.project_id, h.project_name, h.country_name, h.country_code, h.region,
                 h.approval_fiscal_year, h.status, h.closing_date,
                 h.commitment_usd_m AS commitment_usd_millions, i.practice, h.documents_read
          FROM wbg.project_header h
          JOIN wbg.project_info i ON i.project_id = h.project_id
          CROSS JOIN wbg.dashboard_settings st
          WHERE h.documents_read > 0 AND (i.is_ipf OR NOT st.ipf_only)
            AND (%(cohort)s::text IS NULL OR i.cohort_set = %(cohort)s::text)
            AND (%(project)s::text[] IS NULL OR h.project_id = ANY (%(project)s::text[]))
            AND (%(country)s::text[] IS NULL OR h.country_name = ANY (%(country)s::text[])
                 OR h.country_code = ANY (%(country)s::text[]))
            AND (%(region)s::text[] IS NULL OR h.region = ANY (%(region)s::text[]))
            AND (NOT %(restrict)s OR EXISTS (SELECT 1 FROM sel s WHERE s.project_id = h.project_id)))
        SELECT b.*,
               (SELECT count(DISTINCT s.label_set) FROM sel s WHERE s.project_id = b.project_id) AS measures,
               (SELECT count(*) FROM sel s WHERE s.project_id = b.project_id) AS commitments,
               (SELECT count(*) FROM sel s WHERE s.project_id = b.project_id AND s.instrument = 'investment')
                 AS investment,
               (SELECT count(*) FROM sel s WHERE s.project_id = b.project_id AND s.instrument = 'policy')
                 AS policy,
               (SELECT count(DISTINCT a) FROM sel s, unnest(s.asset_arr) a WHERE s.project_id = b.project_id)
                 AS assets,
               (SELECT count(DISTINCT pk) FROM sel s, unnest(s.package_arr) pk WHERE s.project_id = b.project_id)
                 AS packages_linked,
               (SELECT min(s.next_package_date) FROM sel s WHERE s.project_id = b.project_id)
                 AS next_package_date,
               (SELECT array_agg(DISTINCT s.label_set) FROM sel s WHERE s.project_id = b.project_id)
                 AS measures_present,
               fm.ids AS family_measures
        FROM fm LEFT JOIN b ON true
        ORDER BY b.project_name, b.project_id"""), p))
    # one row with no project when there is none: it still carries the family's measures
    ids = list(df["family_measures"].iloc[0]) if len(df) and df["family_measures"].iloc[0] is not None else []
    df = df[df["project_id"].notna()].reset_index(drop=True)
    df.attrs["family_measures"] = ids
    return df


def download(q, level, family=None, *, cohort=None, **filters):
    """A frame to hand to a person as a sheet: fixed, plainly named columns for each level
    (filters choose rows, never columns). level is one of 'project_flags', 'project', 'commitment',
    'sentence', 'package'; family and the filters are those of commitment_detail() (for 'package', of
    packages_detail()). Columns: DOWNLOAD_COLUMNS[level] (project_flags: the project columns
    project_id, project_name, country_name, country_code, region, approval_fiscal_year, then one
    1/0 column per measure of the family, named by the measure's id, 1 where the project has at
    least one commitment of that measure within the filters). The meaning of every column and the
    standing statements are in download_notes()."""
    if level not in DOWNLOAD_LEVELS:
        raise ValueError(f"level must be one of {', '.join(DOWNLOAD_LEVELS)}")
    if level != "package":
        bad = [k for k in _PACKAGE_ONLY if k in filters]
        if bad:
            raise TypeError(f"filter {', '.join(bad)} is of packages: use it with level 'package'")
    if level in ("project_flags", "project"):
        df = _project_frame(q, family, filters, cohort)
        if level == "project":
            return df[[c for c, _ in _PROJECT_COLUMNS]].reset_index(drop=True)
        out = df[[c for c, _ in _PROJECT_FACTS]].copy()
        for m in df.attrs["family_measures"]:
            out[m] = df["measures_present"].map(lambda have, m=m: int(isinstance(have, list) and m in have))
        return out.reset_index(drop=True)
    if level == "commitment":
        src = commitment_detail(q, family, cohort=cohort, **filters)
    elif level == "sentence":
        src = commitment_sentences_detail(q, None, family, cohort=cohort, **filters)
    else:
        src = packages_detail(q, family, cohort=cohort, **filters)
    spec = _DOWNLOAD_SPEC[level]
    out = src[[s for _, s, _ in spec]].copy()
    out.columns = [c for c, _, _ in spec]
    return out.reset_index(drop=True)


DOWNLOAD_COLUMNS = {
    "project_flags": [c for c, _ in _PROJECT_FACTS],       # then one column per measure
    "project": [c for c, _ in _PROJECT_COLUMNS],
    **{k: [c for c, _, _ in v] for k, v in _DOWNLOAD_SPEC.items()},
}


def download_notes(q, level, family=None, filters=None):
    """The rows of a notes sheet to go with download(): one frame of section, item, text.
    Sections: 'Level' (what a row is), 'Column' (each column and what it means), 'Measure'
    (each measure of the family and its definition), 'Data date' (when the data was loaded and
    last fetched), 'Filter' (the family and filters applied, or 'none'), 'Statement' (the three
    standing statements of docs/methodology.md). filters is the dict of filters given to
    download()."""
    if level not in DOWNLOAD_LEVELS:
        raise ValueError(f"level must be one of {', '.join(DOWNLOAD_LEVELS)}")
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, [], "")}
    db_rows = q(_sql("""
        SELECT 'Measure' AS section, label_set AS item,
               label_name || ' (' || coalesce(nullif(family, ''), 'no family') || '): ' || definition AS text
        FROM (SELECT DISTINCT ON (label_set) label_set, label_name, family, definition
              FROM wbg.labels WHERE label_set <> 'assets' AND is_active ORDER BY label_set, label_id) m
        WHERE %(family)s::text IS NULL OR %(family)s::text = ANY (string_to_array(m.family, '|'))
        UNION ALL
        SELECT 'Data date', 'loaded', to_char(loaded_at, 'YYYY-MM-DD') FROM wbg.data_as_of
        UNION ALL
        SELECT 'Data date', 'appraisal documents last fetched', appraisal_fetched_date::text FROM wbg.data_as_of
        UNION ALL
        SELECT 'Data date', 'procurement records last fetched', procurement_fetched_date::text FROM wbg.data_as_of
        UNION ALL
        SELECT 'Data date', 'projects last fetched', projects_fetched_date::text FROM wbg.data_as_of
        ORDER BY 1, 2"""), {"family": family})
    rows = [("Level", level, {"project_flags": "One row per project; a column per measure, 1 when the project has a commitment of that measure.",
                              "project": "One row per project with its facts and counts.",
                              "commitment": "One row per commitment: a project's kept sentences for a measure, grouped by asset type (investment), by measure type (policy, or an investment with no asset), else by paragraph.",
                              "sentence": "One row per sentence of a commitment.",
                              "package": "One row per procurement package linked to a commitment."}[level])]
    if level == "project_flags":
        rows += [("Column", c, m) for c, m in _PROJECT_FACTS]
        rows += [("Column", r["item"], "1 if the project has at least one commitment for measure: " + r["text"] + ". Else 0.")
                 for _, r in db_rows[db_rows["section"] == "Measure"].iterrows()]
    elif level == "project":
        rows += [("Column", c, m) for c, m in _PROJECT_COLUMNS]
    else:
        rows += [("Column", c, m) for c, _, m in _DOWNLOAD_SPEC[level]]
    rows += [(r["section"], r["item"], r["text"]) for _, r in db_rows.iterrows()]
    applied = ([("family", family)] if family else []) + sorted(filters.items())
    rows += [("Filter", k, SEPARATOR.join(v) if isinstance(v, (list, tuple)) else str(v)) for k, v in applied] \
        or [("Filter", "none", "Every row of the dashboard's scope.")]
    rows += [("Statement", str(i + 1), s) for i, s in enumerate(STATEMENTS)]
    return pd.DataFrame(rows, columns=["section", "item", "text"])


# ---------------------------------------------------------------- on the frame

def headline(df):
    """The numbers at the top of a page, from a frame of commitments."""
    if df.empty:
        return {"commitments": 0, "projects": 0, "countries": 0, "measures": 0, "linked_share": None}
    countries = df.loc[~df["is_regional"].fillna(False).astype(bool), "country_code"].dropna()
    return {"commitments": len(df), "projects": df["project_id"].nunique(),
            "countries": countries.nunique(), "measures": df["label_set"].nunique(),
            "linked_share": float((df["stage"] != "No package linked").mean())}


def count(df, *by):
    """Commitments per value of the columns given, largest first."""
    if df.empty:
        return pd.DataFrame(columns=[*by, "commitments"])
    return (df.groupby(list(by), dropna=False).size().rename("commitments").reset_index()
              .sort_values(["commitments", *by], ascending=[False, *[True] * len(by)])
              .reset_index(drop=True))


UNITS = ("commitments", "projects")
# Columns that say something about a project, not about one commitment: a
# share of projects grouped by one of these is taken of the projects with
# the same value.
PROJECT_COLUMNS = ("place", "region", "country_code", "country_name", "approval_fiscal_year",
                   "cohort_set")


def tally(df, *by, unit="commitments", base=None):
    """Commitments, or projects, per value of the columns given, largest
    first: columns *by, n, of, share.

    unit 'commitments' counts rows; 'projects' counts each project once per
    group however many commitments it has there, so the groups do not add
    up to the number of projects. base is a frame of projects (projects(),
    or part of it): with it, `of` is how many base projects the group could
    have held (those sharing the group's project columns, e.g. its region;
    every base project when the group is by measure, asset, type or stage)
    and share is n / of. Without a base, or when counting commitments, of
    and share are empty."""
    cols = [*by, "n", "of", "share"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    g = df.groupby(list(by), dropna=False)
    out = (g["project_id"].nunique() if unit == "projects" else g.size()).rename("n").reset_index()
    out["of"], out["share"] = float("nan"), float("nan")
    if unit == "projects" and base is not None and len(base):
        keys = [c for c in by if c in PROJECT_COLUMNS and c in base.columns]
        if keys:
            size = (base.groupby(keys, dropna=False)["project_id"].nunique().rename("_of").reset_index())
            out = out.merge(size, on=keys, how="left")
            out["of"] = out.pop("_of")
        else:
            out["of"] = base["project_id"].nunique()
        out["share"] = out["n"] / out["of"]
    return (out.sort_values(["n", *by], ascending=[False, *[True] * len(by)])
               .reset_index(drop=True)[cols])


def board(df):
    """The board of where each project stands: one row per stage and project,
    with its commitments and types, in STAGES order then most commitments."""
    if df.empty:
        return pd.DataFrame(columns=["stage", "project_id", "project_name", "place", "commitments", "types"])
    g = (df.groupby(["stage", "project_id", "project_name", "place"], dropna=False)
           .agg(commitments=("commitment_id", "size"),
                types=("type_name", lambda t: sorted({x for x in t if x != NO_TYPE})))
           .reset_index())
    g["order"] = g["stage"].map({s: i for i, s in enumerate(STAGES)})
    return (g.sort_values(["order", "commitments", "project_name"], ascending=[True, False, True])
             .drop(columns="order").reset_index(drop=True))


# ---------------------------------------------------------------- the About page

# The documents the dashboards count: those of an operation the dashboards
# count (project_info.is_ipf while dashboard.ipf_only) that has a document read.
_SCOPE = """
    scope AS (SELECT i.project_id FROM wbg.project_info i
              JOIN wbg.project_header h USING (project_id)
              CROSS JOIN wbg.dashboard_settings s
              WHERE h.documents_read > 0 AND (i.is_ipf OR NOT s.ipf_only)),
    docs AS (SELECT DISTINCT d.document_id FROM wbg.documents d
             JOIN scope ON scope.project_id = ANY (d.project_ids)),
    units AS (SELECT u.unit_id FROM wbg.search_units u JOIN docs USING (document_id))"""

_ABOUT = {
    "projects": """
        SELECT count(*) AS cohort,
               count(*) FILTER (WHERE h.documents_read > 0) AS with_document,
               count(*) FILTER (WHERE h.documents_read > 0 AND (i.is_ipf OR NOT s.ipf_only)) AS in_scope,
               count(*) FILTER (WHERE h.documents_read = 0 AND i.status = 'Pipeline') AS not_approved,
               count(*) FILTER (WHERE h.documents_read = 0 AND i.status IS DISTINCT FROM 'Pipeline'
                                AND i.lending_instrument IN ('Development Policy Lending',
                                                             'Program-for-Results Financing')) AS other_instrument,
               count(*) FILTER (WHERE h.documents_read > 0 AND NOT (i.is_ipf OR NOT s.ipf_only)) AS other_with_document
        FROM wbg.project_info i JOIN wbg.project_header h USING (project_id)
        CROSS JOIN wbg.dashboard_settings s""",
    "text": "WITH" + _SCOPE + """
        SELECT (SELECT count(*) FROM docs) AS documents, (SELECT count(*) FROM units) AS sentences""",
    "measure_routes": "WITH" + _SCOPE + """,
        sc AS (SELECT s.item_id, min(s.round) AS first_round,
                      bool_or(s.route IN ('pattern', 'both')) AS pattern,
                      bool_or(s.route IN ('embedding', 'both')) AS similarity
               FROM wbg.scores s JOIN units ON units.unit_id = s.item_id
               WHERE s.kind = 'measure' GROUP BY s.item_id)
        SELECT count(*) AS graded, count(*) FILTER (WHERE first_round = 0) AS shortlisted,
               count(*) FILTER (WHERE first_round > 0) AS feedback_added,
               count(*) FILTER (WHERE similarity) AS by_similarity,
               count(*) FILTER (WHERE pattern) AS by_pattern FROM sc""",
    "asset_routes": "WITH" + _SCOPE + """,
        sc AS (SELECT s.item_id, min(s.round) AS first_round,
                      bool_or(s.route IN ('pattern', 'both')) AS pattern,
                      bool_or(s.route IN ('embedding', 'both')) AS similarity
               FROM wbg.scores s JOIN units ON units.unit_id = s.item_id
               WHERE s.kind = 'asset' GROUP BY s.item_id)
        SELECT count(*) AS graded, count(*) FILTER (WHERE first_round = 0) AS shortlisted,
               count(*) FILTER (WHERE first_round > 0) AS feedback_added,
               count(*) FILTER (WHERE similarity) AS by_similarity,
               count(*) FILTER (WHERE pattern) AS by_pattern FROM sc""",
    "measures": "WITH" + _SCOPE + """
        SELECT (SELECT count(DISTINCT f.unit_id) FROM wbg.measure_findings f JOIN docs USING (document_id)
                 WHERE f.was_sent_to_model AND f.is_committed) AS at_threshold,
               0 AS overlap_tested,   -- the pair check is gone: nothing is tested for overlap
               (SELECT count(DISTINCT unit_id) FROM wbg.commitment_units) AS kept,
               (SELECT count(DISTINCT u.unit_id) FROM wbg.commitment_units u
                 JOIN wbg.measure_findings f ON f.label_set = u.label_set AND f.unit_id = u.unit_id
                 WHERE cardinality(f.type_ids_above_threshold) > 0) AS with_type,
               (SELECT count(*) FROM wbg.commitment_summary) AS commitments,
               (SELECT count(DISTINCT project_id) FROM wbg.commitment_summary) AS commitment_projects""",
    "assets": "WITH" + _SCOPE + """
        SELECT count(DISTINCT a.unit_id) AS at_threshold
        FROM wbg.unit_assets a JOIN units USING (unit_id) WHERE a.asset_level = 'unit'""",
    "procurement": """
        WITH scope AS (SELECT i.project_id FROM wbg.project_info i
                       JOIN wbg.project_header h USING (project_id)
                       CROSS JOIN wbg.dashboard_settings s
                       WHERE h.documents_read > 0 AND (i.is_ipf OR NOT s.ipf_only))
        SELECT (SELECT count(*) FROM wbg.packages p JOIN scope USING (project_id)) AS packages,
               (SELECT count(DISTINCT p.project_id) FROM wbg.packages p JOIN scope USING (project_id)) AS package_projects,
               (SELECT count(DISTINCT p.plan_document_id) FROM wbg.packages p JOIN scope USING (project_id)) AS plans,
               (SELECT count(*) FROM wbg.notices n JOIN scope USING (project_id)) AS notices,
               (SELECT count(*) FROM wbg.awards a JOIN scope USING (project_id)) AS contracts,
               (SELECT count(DISTINCT a.package_id) FROM wbg.package_assets a JOIN scope USING (project_id)) AS tested,
               (SELECT count(DISTINCT a.package_id) FROM wbg.package_assets a JOIN scope USING (project_id)
                 WHERE a.has_asset) AS with_asset,
               (SELECT count(DISTINCT package_id) FROM wbg.commitment_package_status) AS linked_packages,
               (SELECT count(*) FROM wbg.commitment_summary WHERE linked_packages > 0) AS linked_commitments""",
    "labels": """
        SELECT (SELECT count(DISTINCT label_set) FROM wbg.labels WHERE label_set <> 'assets' AND is_active) AS measures,
               (SELECT count(*) FROM wbg.labels WHERE label_set = 'assets' AND is_active) AS assets,
               (SELECT count(*) FROM wbg.measure_types WHERE type_name NOT LIKE 'OTH.%%') AS types_defined,
               (SELECT count(*) FROM wbg.measure_types WHERE type_name LIKE 'OTH.%%') AS types_proposed""",
    "as_of": "SELECT loaded_at::date AS loaded FROM wbg.data_as_of LIMIT 1",
    "settings": """
        SELECT s.package_link_min_probability AS link_floor, s.pair_check_floor
        FROM wbg.dashboard_settings s""",
}


def about(q):
    """The counts the About page prints on its pipeline figure, each group
    read by its own statement: {group: {name: value}}. A group whose
    statement fails (a table not yet loaded, a timeout) is an empty dict,
    so the page still draws and leaves that count out. Every count is of the
    operations the dashboards count (dashboard.ipf_only) and their documents.
    The thresholds the page quotes come from the control panel."""
    from wbg_portfolio import panel
    out = {}
    # floor.default is now only the cut the evaluation reads a first-pass score at; the commitment
    # is the band rule, so band.low, band.keep and band.context are given too. floor.cyber_security
    # stays as a key but is always None: there are no per-measure floors any more.
    floors = {"floor.cyber_security": None}
    for key in ("floor.default", "floor.types", "asset.floor", "link.asset_yes", "band.low", "band.keep",
                "band.context"):
        v = panel.get(key)
        floors[key] = None if v is None else float(v)
    out["floors"] = floors
    for name, sql in _ABOUT.items():
        try:
            df = q(_sql(sql), None)
            out[name] = {} if df.empty else {k: (None if pd.isna(v) else v) for k, v in df.iloc[0].items()}
        except Exception:
            out[name] = {}
    try:
        g = q(f"SELECT count(*) AS graded_sentences FROM {db.STORE}.grades WHERE kind = 'measure'", None)
        out["grades"] = {"graded_sentences": int(g.iloc[0, 0])}
    except Exception:
        out["grades"] = {}
    return out
