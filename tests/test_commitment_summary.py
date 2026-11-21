"""wbg.commitment_summary, the one summary of a commitment the dashboards and the app read (the invented
dataset of tests/demo_seed.py, published with the real views)."""
import pytest

import demo_seed
from wbg_portfolio import db


@pytest.fixture
def demo(pg):
    demo_seed.publish(pg, demo_seed.Demo(seed=5, n_projects=12).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    return pg


def test_asset_from_is_the_best_place_any_sentence_found_its_asset(demo):
    s = db.SCHEMA
    order = {"sentence": 0, "second_look": 1, "paragraph": 2, "none": 3}
    units = {}
    for cid, af in db.rows(demo, f"SELECT commitment_id, asset_from FROM {s}.commitment_units"):
        units.setdefault(cid, set()).add(af)
    got = dict(db.rows(demo, f"SELECT commitment_id, asset_from FROM {s}.commitment_summary"))
    assert got and set(got) == set(units)
    for cid, afs in units.items():
        assert got[cid] == min(afs, key=order.get), cid
    assert any(len(a) > 1 and "none" in a for a in units.values()), "the demo mixes a found asset and none"
