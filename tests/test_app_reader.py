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
    assert list(app.coming_up(lambda sql, params: rows.iloc[0:0], None).columns)[-1] == "place"


def test_about_leaves_out_a_group_that_fails():
    def q(sql, params):
        if "data_as_of" in sql:
            raise RuntimeError("no such view")
        return pd.DataFrame({"cohort": [3], "in_scope": [2]})
    out = app.about(q)
    assert out["as_of"] == {} and out["projects"]["cohort"] == 3
    assert out["floors"]["floor.default"] is not None
