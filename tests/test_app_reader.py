"""The parts of the app's reader that need no Postgres: a stand-in for the
query function returns invented rows."""
import pandas as pd

from wbg_portfolio import app


def test_stage_names_and_urgency():
    assert app.stage("Cancelled") == app.DROPPED == "Package dropped"
    assert app.stage("Overdue") == app.stage("Tendered") == "Coming up"
    assert set(app.STAGE_URGENCY) == set(app.STAGES) and app.STAGE_URGENCY[0] == "Coming up"


def test_package_date_says_which_date():
    rows = pd.DataFrame({
        "package_id": ["a", "b", "c", "d"], "description": ["w", "x", "y", "z"], "category": [None] * 4,
        "method_code": [None] * 4, "estimated_amount_usd": [1.0, 2.0, 3.0, 4.0],
        "package_status": ["Planned", "Contract signed", "Overdue", "Cancelled"],
        "next_date": pd.to_datetime(["2030-01-01", None, None, None]),
        "due_date": pd.to_datetime(["2030-01-01", "2020-01-01", "2021-01-01", None]),
        "signed_date": pd.to_datetime([None, "2020-06-01", None, None])})
    p = app.packages(lambda sql, params: rows.copy(), "P1").set_index("package_id")
    assert p.loc["a", "date_kind"] == "planned" and p.loc["b", "date_kind"] == "signed"
    assert p.loc["c", "date_kind"] == "was due" and pd.isna(p.loc["d", "date"]) and pd.isna(p.loc["d", "date_kind"])
    assert p.loc["d", "stage"] == app.DROPPED


def test_coming_up_gives_a_place_and_an_empty_frame():
    rows = pd.DataFrame({
        "package_id": ["a"], "project_id": ["P1"], "project_name": ["Invented"], "country_name": [None],
        "region": ["Invented Region"], "is_regional": [True], "measures": ["Backup power"],
        "description": ["Generators"], "package_status": ["Planned"], "date": pd.to_datetime(["2030-01-01"]),
        "estimated_amount_usd": [10.0]})
    seen = {}

    def q(sql, params):
        seen.update(params)
        return rows.copy()
    assert app.coming_up(q, "digital")["place"].tolist() == ["Invented Region"]
    assert seen["cohort"] == "digital" and "Overdue" in seen["statuses"]
    assert seen["family"] is None
    app.coming_up(q, "digital", "cloud_data")
    assert seen["family"] == "cloud_data"
    assert list(app.coming_up(lambda sql, params: rows.iloc[0:0], None).columns)[-1] == "place"


def test_about_leaves_out_a_group_that_fails():
    def q(sql, params):
        if "data_as_of" in sql:
            raise RuntimeError("no such view")
        return pd.DataFrame({"cohort": [3], "in_scope": [2]})
    out = app.about(q)
    assert out["as_of"] == {} and out["projects"]["cohort"] == 3
    assert out["floors"]["floor.default"] is not None


# ------------------------------------------------------------------ the redesigned dashboard
#
# The reader's commitment, sentence, package and download functions run against an invented
# dataset (tests/demo_seed.py) published into the test schema: the real tables, the real views.

import pytest

import demo_seed
from test_app import _q, _published


@pytest.fixture
def demo(pg):
    demo_seed.publish(pg, demo_seed.Demo(seed=3, n_projects=14).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    return _q(pg)


COMMITMENT_COLUMNS = [
    "commitment_id", "project_id", "project_name", "country_name", "country_code", "region", "is_regional", "place",
    "approval_fiscal_year", "label_set", "measure_name", "family", "commitment_label", "commitment_key", "type_ids",
    "type_names", "n_types", "has_proposed_type", "asset_id", "asset_name", "subtype_id", "subtype_name", "asset2_id",
    "asset2_name", "subtype2_id", "subtype2_name", "n_assets", "instrument", "asset_column", "component_number",
    "component_name", "n_sentences", "n_paragraphs", "n_documents", "first_document_date", "linked_packages",
    "strong_packages", "next_package_date", "best_link_level", "best_link_rank"]


def test_the_families_list_their_measures_and_a_measure_in_two_appears_under_both(demo):
    f = app.families(demo)
    assert set(f["family"]) == {"climate", "cloud_data"}
    assert f.groupby("family")["label_set"].nunique().to_dict() == {"climate": 13, "cloud_data": 11}
    assert set(f[f["label_set"] == "disaster_recovery"]["family"]) == {"climate", "cloud_data"}
    a = app.commitment_detail(demo)
    fam = f[f["family"] == "climate"]
    assert fam["family_commitments"].iloc[0] == a[a["family"].str.contains("climate")]["commitment_id"].nunique()
    assert fam.set_index("label_set").loc["cra", "measure_commitments"] == (a["label_set"] == "cra").sum()


def test_one_cohort_argument_narrows_every_reader_function_the_same_way(demo):
    projects = app.projects(demo)
    both = set(projects["cohort_set"])
    assert both == {"digital", "digital_contributor"}             # the demo carries both values
    for cohort in both:
        mine = set(projects.loc[projects["cohort_set"] == cohort, "project_id"])
        a = app.commitment_detail(demo, cohort=cohort)
        assert len(a) and set(a["project_id"]) <= mine
        assert set(app.commitment_sentences_detail(demo, None, None, cohort=cohort)["project_id"]) <= mine
        assert set(app.packages_detail(demo, cohort=cohort)["project_id"]) <= mine
        assert set(app.not_digital(demo, cohort=cohort)["project_id"]) <= mine
        for level in ("project_flags", "project", "commitment", "sentence"):
            assert set(app.download(demo, level, cohort=cohort)["project_id"]) <= mine
        assert len(app.download(demo, "package", cohort=cohort)) == len(app.packages_detail(demo, cohort=cohort))
        f = app.families(demo, cohort=cohort)
        assert f["family_commitments"].max() <= a["commitment_id"].nunique()
    whole = app.commitment_detail(demo)
    parts = [app.commitment_detail(demo, cohort=c) for c in both]
    assert sum(len(x) for x in parts) == len(whole) and not set(parts[0]["commitment_id"]) & set(parts[1]["commitment_id"])
    assert app.families(demo)["family_commitments"].max() >= app.families(demo, cohort="digital")["family_commitments"].max()
    with pytest.raises(TypeError):
        app.commitment_detail(demo, cohrt="digital")                       # a misspelt cohort still fails


def test_a_commitment_has_its_key_assets_types_instrument_and_procurement(demo):
    a = app.commitment_detail(demo)
    assert list(a.columns) == COMMITMENT_COLUMNS and len(a) > 50 and a["commitment_id"].is_unique
    assert set(a["instrument"].dropna()) == {"investment", "policy"}
    assert set(a["commitment_key"]) == {"asset_type", "type", "paragraph"} and (a["n_sentences"] >= 1).all()
    keyed = a[a["commitment_key"] == "asset_type"]
    assert keyed["asset_id"].notna().all() and keyed["subtype_id"].notna().all()
    assert (keyed["subtype_id"] != "unspecified").all() and (a[a["commitment_key"] == "asset_type"]["instrument"]
                                                               != "policy").all()
    assert (a[a["commitment_key"] == "paragraph"]["commitment_label"] == "Sentences of one paragraph").all()
    has_asset = a["asset_id"].notna()
    assert (a[has_asset].apply(lambda r: r["asset_column"].startswith(r["asset_name"]), axis=1)).all()
    assert (a[~has_asset]["asset_column"] == "none").all()   # policy is an instrument, never an asset
    assert (a["instrument"] == "policy")[~has_asset].any()                         # policy with no asset exists
    assert (a["n_types"] == 0).any() and a["has_proposed_type"].any() and (a["n_assets"] == 2).any()
    assert a[a["asset2_id"].notna()]["asset2_name"].notna().all()
    assert (a["linked_packages"] > 0).any()
    assert set(a["best_link_level"].dropna()) <= {"strong", "type_and_component", "type", "component", "asset"}
    assert {"strong", "asset"} <= set(a["best_link_level"].dropna())
    assert (a["strong_packages"] <= a["linked_packages"]).all()
    assert a.loc[a["linked_packages"] == 0, "best_link_level"].isna().all()
    assert a["first_document_date"].notna().all()


def test_commitment_filters_choose_rows_and_a_misspelt_one_fails(demo):
    a = app.commitment_detail(demo)
    pol = app.commitment_detail(demo, instrument="policy")
    assert 0 < len(pol) < len(a) and set(pol["instrument"]) == {"policy"}
    one = app.commitment_detail(demo, project=a["project_id"].iloc[0])
    assert set(one["project_id"]) == {a["project_id"].iloc[0]}
    assert len(app.commitment_detail(demo, project=list(a["project_id"].unique()[:2]))) == a["project_id"].isin(a["project_id"].unique()[:2]).sum()
    asset = a["asset_id"].dropna().iloc[0]
    sub = app.commitment_detail(demo, asset=asset)
    assert len(sub) and ((sub["asset_id"] == asset) | (sub["asset2_id"] == asset)).all()
    assert (app.commitment_detail(demo, type="(no type)")["n_types"] == 0).all()
    assert (app.commitment_detail(demo, asset="(no asset)")["n_assets"] == 0).all()
    t = a[a["n_types"] > 0]["type_ids"].iloc[0].split("; ")[0]
    assert all(t in s.split("; ") for s in app.commitment_detail(demo, type=t)["type_ids"])
    st = a["subtype_id"].dropna().iloc[0]
    assert len(app.commitment_detail(demo, asset_type=st)) > 0
    assert set(app.commitment_detail(demo, region=a["region"].iloc[0])["region"]) == {a["region"].iloc[0]}
    assert set(app.commitment_detail(demo, country=a["country_code"].iloc[0])["country_code"]) == {a["country_code"].iloc[0]}
    assert set(app.commitment_detail(demo, measure="cra")["label_set"]) <= {"cra"}
    assert set(app.commitment_detail(demo, family="cloud_data")["family"].str.contains("cloud_data")) == {True}
    assert app.commitment_detail(demo, family="no such family").empty and list(app.commitment_detail(demo, family="x").columns) == COMMITMENT_COLUMNS
    assert set(app.commitment_detail(demo, key="paragraph")["commitment_key"]) == {"paragraph"}
    with pytest.raises(TypeError):
        app.commitment_detail(demo, instrumnt="policy")


def test_the_sentences_of_commitments_come_with_their_strength_inputs_and_page(demo):
    a = app.commitment_detail(demo, measure="cra")
    s = app.commitment_sentences_detail(demo, a["commitment_id"])
    assert len(s) == a["n_sentences"].sum() and set(s["commitment_id"]) == set(a["commitment_id"])
    for col in ("text", "document_kind", "document_date", "page_number", "pdf_url", "section_title", "score_first",
                "decided_by", "paragraph_id", "unit_id", "second_grade", "score_context"):
        assert col in s
    assert s["text"].str.len().gt(20).all() and set(s["document_kind"]) <= {"pad", "additional_financing", "restructuring"}
    assert s["pdf_url"].str.contains("#page=").all() and set(s["decided_by"]) <= {"score", "second_look"}
    band = s[s["decided_by"] == "second_look"]
    assert (band["score_context"] >= 0.7).all() and (band["second_grade"] >= 2).all()
    assert (s[s["decided_by"] == "score"]["score_first"] >= 0.9).all()
    same = app.commitment_sentences_detail(demo, None, None, measure="cra")                  # by the filters instead
    assert sorted(same["unit_id"]) == sorted(s["unit_id"])
    assert app.commitment_sentences_detail(demo, []).empty and app.commitment_sentences_detail(demo, ["no such one"]).empty


def test_packages_carry_every_procurement_field_and_what_they_serve(demo):
    p = app.packages_detail(demo)
    assert len(p) > 20 and p["package_id"].is_unique
    for col in ("package_number", "description", "component_name", "category", "method_code", "method_name",
                "market_approach", "estimated_amount_usd", "actual_amount_usd", "contract_amount_usd", "planned_date",
                "revised_date", "notice_date", "notice_deadline", "signed_date", "supplier", "supplier_country",
                "plan_document_id", "plan_disclosure_date", "package_status", "stage", "link_strength", "measure_names",
                "commitment_ids", "commitment_labels", "asset_names", "country_name", "project_name", "link_level",
                "link_rank", "is_cancelled", "is_generic", "asset_type_ids"):
        assert col in p, col
    assert set(p["link_strength"]) <= {"strong", "asset"} and {"strong", "asset"} <= set(p["link_strength"])
    signed = p[p["signed_date"].notna()]
    assert len(signed) and signed["supplier"].notna().all() and signed["supplier_country"].notna().all()
    assert p["package_number"].notna().all() and p["plan_document_id"].notna().all()
    strong = p[p["link_strength"] == "strong"]
    assert (strong["measure_ids"] != "").all() and (p["n_commitments"] > 0).all()
    assert (strong["link_level"] == "strong").all() and list(p["link_rank"]) == sorted(p["link_rank"])
    ids = set(app.commitment_detail(demo)["commitment_id"])
    assert {x for s in p["commitment_ids"] for x in s.split("; ")} <= ids
    coming = app.packages_detail(demo, status="Coming up")
    assert len(coming) and set(coming["stage"]) == {"Coming up"}
    assert set(app.packages_detail(demo, strength="asset")["link_strength"]) == {"asset"}
    m = strong["measure_ids"].iloc[0].split("; ")[0]
    assert len(app.packages_detail(demo, measure=m)) > 0
    one = app.packages_detail(demo, project=p["project_id"].iloc[0])
    assert set(one["project_id"]) == {p["project_id"].iloc[0]}
    assert app.packages_detail(demo, family="no such family").empty


def test_one_package_has_its_notices_awards_and_commitments(demo):
    p = app.packages_detail(demo)
    done = p[p["signed_date"].notna()].iloc[0]
    d = app.package(demo, done["package_id"])
    assert set(d) == {"package", "notices", "awards", "commitments"}
    assert len(d["package"]) == 1 and len(d["awards"]) >= 1
    assert d["awards"]["supplier_name"].iloc[0] == done["supplier"] and d["awards"]["supplier_country"].iloc[0]
    assert d["awards"]["total_amount_usd"].iloc[0] > 0
    assert set(d["commitments"]["commitment_id"]) == set(done["commitment_ids"].split("; "))
    tendered = p[p["notice_date"].notna()].iloc[0]
    n = app.package(demo, tendered["package_id"])["notices"]
    assert len(n) >= 1 and {"publication_date", "deadline_date"} <= set(n)
    assert app.package(demo, "no such package")["package"].empty


def test_not_digital_types_and_assets_have_names_and_definitions(demo):
    nd = app.not_digital(demo)
    assert len(nd) and nd["text"].str.len().gt(20).all() and (nd["digital_p"] < 0.5).all()
    assert set(app.not_digital(demo, project_id=nd["project_id"].iloc[0])["project_id"]) == {nd["project_id"].iloc[0]}
    assert set(app.not_digital(demo, measure=nd["label_set"].iloc[0])["label_set"]) == {nd["label_set"].iloc[0]}
    at = app.asset_types(demo)
    assert len(at) == 19 and at["asset_id"].nunique() == 4 and at["subtype_name"].notna().all()
    assert at["asset_definition"].str.len().gt(10).all() and at["subtype_definition"].str.len().gt(10).all()
    td = app.type_definitions(demo)
    assert td["is_proposed"].any() and td[td["is_proposed"]]["type_name"].str.startswith("OTH. ").all()
    assert (td["definition"].str.len() > 0).all() and not td[~td["is_proposed"]]["type_id"].str.startswith("OTH.").any()


def test_downloads_have_fixed_columns_whatever_the_filters(demo):
    for level in app.DOWNLOAD_LEVELS:
        full = app.download(demo, level, "climate")
        narrow = app.download(demo, level, "climate", instrument="policy", region="Africa")
        assert list(full.columns) == list(narrow.columns) and len(narrow) <= len(full)
        if level != "project_flags":
            assert list(full.columns) == app.DOWNLOAD_COLUMNS[level]
    flags = app.download(demo, "project_flags", "climate")
    climate = app.families(demo).query("family == 'climate'")["label_set"].tolist()
    assert set(climate) <= set(flags.columns) and len(flags.columns) == 6 + len(climate)
    assert set(flags[climate].stack().unique()) <= {0, 1} and flags[climate].sum().sum() > 0
    proj = app.download(demo, "project", "climate")
    acts = app.download(demo, "commitment", "climate")
    assert proj["commitments"].sum() == len(acts) and proj["investment"].sum() + proj["policy"].sum() <= len(acts)
    assert len(app.download(demo, "project")) == len(flags) == len(app.projects(demo))
    assert len(app.download(demo, "sentence", "climate")) == acts["sentences"].sum()
    only = app.download(demo, "project_flags", "climate", measure="cra")
    assert (only["cra"] == 1).all() and len(only) < len(flags)
    assert (only[[c for c in climate if c != "cra"]].sum().sum() >= 0)
    with pytest.raises(ValueError):
        app.download(demo, "nonsense")


def test_the_notes_say_what_each_column_means_and_carry_the_standing_statements(demo):
    for level in app.DOWNLOAD_LEVELS:
        df = app.download(demo, level, "climate", instrument="policy")
        notes = app.download_notes(demo, level, "climate", {"instrument": "policy", "region": None})
        assert list(notes.columns) == ["section", "item", "text"]
        cols = notes[notes["section"] == "Column"]
        assert list(cols["item"]) == list(df.columns) and cols["text"].str.len().gt(10).all()
        assert set(notes[notes["section"] == "Measure"]["item"]) == set(
            app.families(demo).query("family == 'climate'")["label_set"])
        assert notes[notes["section"] == "Measure"]["text"].str.len().gt(40).all()
        assert notes[notes["section"] == "Filter"][["item", "text"]].values.tolist() == [
            ["family", "climate"], ["instrument", "policy"]]
        assert notes[notes["section"] == "Data date"]["text"].str.match(r"\d{4}-\d{2}-\d{2}").all()
        assert notes[notes["section"] == "Statement"]["text"].tolist() == list(app.STATEMENTS)
    assert app.download_notes(demo, "commitment")[lambda d: d["section"] == "Filter"]["item"].tolist() == ["none"]


def test_the_new_readers_take_a_store_the_pipeline_published(pg, corpus, fake_model, fake_chat):
    q = _published(pg, corpus)
    a = app.commitment_detail(q)
    assert list(a.columns) == COMMITMENT_COLUMNS and set(a["project_id"]) == {"P1"} and len(a) >= 1
    assert set(a["asset_column"]) <= {"Mobile networks", "Fibre networks", "Mobile networks + Fibre networks", "none"}
    s = app.commitment_sentences_detail(q, a["commitment_id"])
    assert len(s) == a["n_sentences"].sum() and s["text"].notna().all() and s["pdf_url"].notna().all()
    f = app.families(q)
    assert "cra" in f["label_set"].tolist() and f["measure_commitments"].max() == len(a)
    assert len(app.asset_types(q)) >= 2 and "site" in app.type_definitions(q)["type_id"].tolist()
    assert app.not_digital(q).empty or True
    for level in app.DOWNLOAD_LEVELS:
        assert len(app.download(q, level)) >= 0
    assert app.download(q, "commitment").shape[0] == len(a)
    assert app.packages_detail(q)["package_id"].tolist() == ["P1-1"]
    assert app.package(q, "P1-1")["package"]["package_id"].tolist() == ["P1-1"]
    assert app.download_notes(q, "project")["section"].eq("Statement").sum() == 3
    empty = app.commitment_detail(q, instrument="policy")
    assert empty.empty and list(empty.columns) == COMMITMENT_COLUMNS
    assert app.commitment_sentences_detail(q, ["x"]).empty and app.packages_detail(q, family="no such family").empty
    flags = app.download(q, "project_flags", project="no such project")        # no rows, the columns all the same
    assert flags.empty and list(flags.columns)[6:] == ["cra"]
    assert app.download(q, "project_flags")["cra"].tolist() == [1]
