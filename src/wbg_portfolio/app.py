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
STAGES = ("Completed", "Under way", "Coming up", "Dropped", "No package linked")
STAGE_OF_STATUS = {
    "Contract completed": "Completed",
    "Under implementation": "Under way", "Contract signed": "Under way",
    "Overdue": "Coming up", "Planned": "Coming up", "Tendered": "Coming up",
    "Not in latest plan": "Dropped", "Cancelled": "Dropped",
    "No package found": "No package linked",
}
NO_ASSET = "(no asset)"
NO_TYPE = "(no type)"


def _sql(text):
    """The views live in schema wbg; a test store names its own (db.SCHEMA)."""
    return text.replace("wbg.", db.SCHEMA + ".") if db.SCHEMA != "wbg" else text


def stage(status):
    return STAGE_OF_STATUS.get(status, "No package linked")


def commitments(q, cohort=None):
    """One row per commitment (project x asset x measure x type) with its
    place, the project's approval year and its procurement stage. cohort:
    None for every project, else a cohort_set ('digital', 'digital_contributor')."""
    df = q(_sql("""
        SELECT c.commitment_id, c.project_id, c.project_name, c.cohort_set, c.country_name,
               c.country_code, c.region, c.is_regional, c.label_set, c.measure_name,
               c.asset_id, c.asset_name, c.type_id, c.type_name, c.status, c.next_date,
               c.sentences, c.strongest_p, c.linked_packages, h.approval_fiscal_year
        FROM wbg.commitments c
        LEFT JOIN wbg.project_header h USING (project_id)
        WHERE %(cohort)s::text IS NULL OR c.cohort_set = %(cohort)s
        ORDER BY c.project_name, c.measure_name, c.asset_name, c.type_name"""), {"cohort": cohort})
    if df.empty:
        return df.assign(stage=pd.Series(dtype=str), place=pd.Series(dtype=str))
    df["asset_name"] = df["asset_name"].fillna(NO_ASSET)
    df["type_name"] = df["type_name"].fillna(NO_TYPE)
    df["stage"] = df["status"].map(stage)
    # A regional project has no country: its region stands in for it.
    df["place"] = df["country_name"].where(~df["is_regional"].fillna(False).astype(bool), df["region"])
    return df


def evidence(q, commitment_ids):
    """The strongest sentence of each commitment asked for: its text, page,
    PDF link, probability and document kind."""
    ids = list(dict.fromkeys(commitment_ids))
    if not ids:
        return pd.DataFrame(columns=["commitment_id", "text", "page_number", "pdf_url",
                                     "posterior_probability", "document_kind"])
    return q(_sql("""
        SELECT DISTINCT ON (commitment_id) commitment_id, text, page_number, pdf_url,
               posterior_probability, document_kind
        FROM wbg.commitment_sentences
        WHERE commitment_id = ANY(%(ids)s)
        ORDER BY commitment_id, posterior_probability DESC, length(text), unit_id"""), {"ids": ids})


def measures(q):
    """One row per measure: its definition, counts and the graded sheet's
    precision (graded_sheet says whether it is an evaluation or a training
    draw; a training draw's figure is provisional)."""
    return q(_sql("""
        SELECT label_set, measure_name, definition, commitments, projects, countries, types,
               graded_sheet, graded_units, precision
        FROM wbg.measure_summary ORDER BY commitments DESC NULLS LAST, measure_name"""), None)


def measure_types(q, label_set=None):
    """Each approved type of each measure (or of one), with its definition."""
    return q(_sql("""
        SELECT DISTINCT label_set, type_id, type_name, definition
        FROM wbg.measure_types
        WHERE %(set)s::text IS NULL OR label_set = %(set)s
        ORDER BY label_set, type_name"""), {"set": label_set})


def assets(q):
    """The digital assets (inputs/taxonomy/assets.csv as loaded): id, name, definition."""
    return q(_sql("""
        SELECT label_id AS asset_id, label_name AS asset_name, definition
        FROM wbg.labels WHERE label_set = 'assets' AND is_active
        ORDER BY label_name"""), None)


def project(q, project_id):
    """Everything the project view shows beside its commitments: the header
    (one row), the assets its sentences name, its components with cost, the
    documents read, and its linked packages with their stage."""
    p = {"pid": project_id}
    return {
        "header": q(_sql("SELECT * FROM wbg.project_header WHERE project_id = %(pid)s"), p),
        "assets": q(_sql("""
            SELECT asset_id, asset_name, sentences_naming_it, sentences_from_paragraph, best_probability
            FROM wbg.project_assets WHERE project_id = %(pid)s
            ORDER BY sentences_naming_it DESC NULLS LAST, asset_name"""), p),
        "components": q(_sql("""
            SELECT component_level, component_number, component_name, cost_usd_millions, cost_covers
            FROM wbg.project_components WHERE project_id = %(pid)s
            ORDER BY component_number"""), p),
        "documents": q(_sql("""
            SELECT document_id, document_kind, document_date, title, pdf_url
            FROM wbg.project_documents WHERE project_id = %(pid)s ORDER BY document_date"""), p),
        "packages": packages(q, project_id),
    }


def packages(q, project_id):
    """The packages linked to a project's commitments, once each, with the
    date that matters (next date while still to come, else the due date)."""
    df = q(_sql("""
        SELECT DISTINCT ON (package_id) package_id, description_clean AS description, category,
               method_code, estimated_amount_usd, package_status, next_date, due_date, signed_date
        FROM wbg.commitment_package_status WHERE project_id = %(pid)s
        ORDER BY package_id, link_probability DESC"""), {"pid": project_id})
    if df.empty:
        return df.assign(stage=pd.Series(dtype=str), date=pd.Series(dtype="datetime64[ns]"))
    df["stage"] = df["package_status"].map(stage)
    df["date"] = df["next_date"].fillna(df["signed_date"]).fillna(df["due_date"])
    return df.sort_values(["date", "package_id"], na_position="last").reset_index(drop=True)


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
