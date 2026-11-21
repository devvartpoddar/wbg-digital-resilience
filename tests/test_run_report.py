"""The run note's spend table: every ledger step must fold into a pipeline stage.

The note prints one row per pipeline stage and sums only those rows, so a ledger step
left under its own name (`asset_second`, `second:cra:chat`, `policy`) is dropped from
the table and the run's cost is understated. Found on the wbg_v2 full run of
2026-11-20: US$5.998 of spend printed as US$1.70.
"""
from wbg_portfolio.run_report import fold_spend

# the step names the wbg_v2 full run wrote to wbg_store.spend
LEDGER = [
    ("classify", 1, 3, 0.03),
    ("asset_second", 1, 5_149, 1.61),
    ("asset_subtype", 1, 2_510, 0.32),
    ("measure:cra", 2, 1_742, 0.43),
    ("second:data_governance:chat", 2, 3_370, 0.19),
    ("policy", 1, 5_149, 0.41),
    ("digital", 1, 467, 0.19),
    ("types:generate", 1, 23, 0.05),
    ("link:assets", 1, 247, 0.12),
    ("package_asset_second", 1, 3_562, 0.19),
    ("package_measure:cra", 1, 30, 0.01),
]


def test_every_step_folds_into_a_stage_the_note_prints():
    got = {g: (c, k) for g, c, k in fold_spend(LEDGER)}
    assert set(got) == {"classify", "measure", "link"}, sorted(got)


def test_the_folded_figures_and_the_total():
    got = {g: (c, k) for g, c, k in fold_spend(LEDGER)}
    assert got["classify"][0] == 7_662                      # the asset map's own pass, second look and kinds
    assert round(got["classify"][1], 2) == 1.96
    assert got["measure"][0] == 10_751
    assert round(got["measure"][1], 2) == 1.27
    assert got["link"][0] == 3_839
    assert round(got["link"][1], 2) == 0.32
    # nothing is lost: the calls and the money of every row are in the table
    assert sum(c for c, _ in got.values()) == 22_252
    assert round(sum(k for _, k in got.values()), 2) == 3.55


def test_an_unknown_step_keeps_its_own_name_and_is_not_charged_to_a_stage():
    got = {g: (c, k) for g, c, k in fold_spend([("widget", 1, 2, 0.5)])}
    assert got == {"widget": (2, 0.5)}


def test_no_rows_is_no_table():
    assert fold_spend([]) == []
    assert fold_spend(None) == []
