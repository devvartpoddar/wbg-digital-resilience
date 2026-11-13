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
# package (wbg.commitments.status, docs/method/dashboards.md "A commitment's
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
    """One row per commitment (project x asset x measure x type) with its
    place, the project's approval year and its procurement stage. cohort:
    None for every project, else a cohort_set ('digital', 'digital_contributor')."""
    df = _numbers(q(_sql("""
        SELECT c.commitment_id, c.project_id, c.project_name, c.cohort_set, c.country_name,
               c.country_code, c.region, c.is_regional, c.label_set, c.measure_name,
               c.asset_id, c.asset_name, c.type_id, c.type_name, c.status, c.next_date,
               c.sentences, c.strongest_p, c.linked_packages, h.approval_fiscal_year
        FROM wbg.commitments c
        LEFT JOIN wbg.project_header h USING (project_id)
        WHERE %(cohort)s::text IS NULL OR c.cohort_set = %(cohort)s
        ORDER BY c.project_name, c.measure_name, c.asset_name, c.type_name"""), {"cohort": cohort}))
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
        SELECT label_set, measure_name, definition, commitments, projects, countries, types,
               graded_sheet, graded_units, precision
        FROM wbg.measure_summary ORDER BY commitments DESC NULLS LAST, measure_name"""), None))


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
        ORDER BY package_id, link_probability DESC"""), {"pid": project_id}))
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


def coming_up(q, cohort=None):
    """The packages still to come that are linked to a commitment, soonest
    first: one row per package, with its project, the measures of the
    commitments it is linked to, its status (Overdue, Planned or Tendered),
    the date to act on (a future due date or tender deadline; for an overdue
    package the date it was due) and the plan's estimate. cohort as in
    commitments()."""
    cols = ["package_id", "project_id", "project_name", "country_name", "region", "is_regional",
            "measures", "description", "package_status", "date", "estimated_amount_usd"]
    df = _numbers(q(_sql("""
        SELECT s.package_id, c.project_id, c.project_name, c.country_name, c.region, c.is_regional,
               string_agg(DISTINCT c.measure_name, '; ' ORDER BY c.measure_name) AS measures,
               s.description_clean AS description, s.package_status,
               coalesce(s.next_date, s.due_date) AS date, s.estimated_amount_usd
        FROM wbg.commitment_package_status s
        JOIN wbg.commitments c USING (commitment_id)
        WHERE s.package_status = ANY(%(statuses)s)
          AND (%(cohort)s::text IS NULL OR c.cohort_set = %(cohort)s)
        GROUP BY s.package_id, c.project_id, c.project_name, c.country_name, c.region, c.is_regional,
                 s.description_clean, s.package_status, s.next_date, s.due_date, s.estimated_amount_usd
        ORDER BY coalesce(s.next_date, s.due_date) NULLS LAST, c.project_name, s.package_id"""),
        {"statuses": list(COMING_UP_STATUSES), "cohort": cohort}))
    if df.empty:
        return pd.DataFrame(columns=[*cols, "place"])
    df["place"] = df["country_name"].where(~df["is_regional"].fillna(False).astype(bool), df["region"])
    return df


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
               (SELECT count(DISTINCT c.unit_id) FROM wbg.unit_pair_checks c JOIN units USING (unit_id)) AS overlap_tested,
               (SELECT count(DISTINCT unit_id) FROM wbg.commitment_units) AS kept,
               (SELECT count(DISTINCT unit_id) FROM wbg.commitment_units WHERE type_id <> '(no type)') AS with_type,
               (SELECT count(*) FROM wbg.commitments) AS commitments,
               (SELECT count(DISTINCT project_id) FROM wbg.commitments) AS commitment_projects""",
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
                 WHERE coalesce(a.posterior_probability, a.probability) >= {{asset_yes}}) AS with_asset,
               (SELECT count(DISTINCT package_id) FROM wbg.commitment_package_status) AS linked_packages,
               (SELECT count(*) FROM wbg.commitments WHERE linked_packages > 0) AS linked_commitments""",
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
    floors = {}
    for key in ("floor.default", "floor.cyber_security", "floor.types", "asset.floor", "link.asset_yes"):
        try:
            floors[key] = float(panel.require(key))
        except Exception:
            floors[key] = None
    out["floors"] = floors
    for name, sql in _ABOUT.items():
        try:
            if "{{asset_yes}}" in sql:
                sql = sql.replace("{{asset_yes}}", repr(float(floors["link.asset_yes"])))
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
