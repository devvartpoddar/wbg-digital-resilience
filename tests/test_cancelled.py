"""One rule in Python and in the views: which plan statuses mean a package was cancelled (decide.CANCELLED:
the links' package_cancelled flag and the views' wbg.is_cancelled read the same list), and when two
component names are one component (keys.same_component and wbg.same_component)."""
import demo_seed
from wbg_portfolio import db
from wbg_portfolio import decide


def test_the_python_rule():
    assert decide.CANCELLED == ("Canceled", "Terminated")
    assert decide.is_cancelled("Terminated") and decide.is_cancelled("Canceled")
    assert not any(decide.is_cancelled(s) for s in ("Pending", "Signed", "Completed", "", None))


def test_the_views_read_the_same_list(pg):
    demo_seed.publish(pg, demo_seed.Demo(seed=5, n_projects=6).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    for status in ("Canceled", "Terminated", "Pending", "Signed", None):
        got = db.one(pg, f"SELECT {db.SCHEMA}.is_cancelled(%s)", (status,))[0]
        assert got == decide.is_cancelled(status), status


def test_the_views_name_rule_is_the_component_readers(pg):
    from wbg_portfolio import keys
    demo_seed.publish(pg, demo_seed.Demo(seed=5, n_projects=6).build().finish_tables(), log=lambda *_: None)
    pg.commit()
    for a, b in (("projectmanagement", "projectmanagementandinstitutions"), ("roads", "roadsandbridges"),
                 ("datacentre", "datacentre"), ("", ""), ("abcdefghijkl", "abcdefghijklm"),
                 ("abcdefghijk", "abcdefghijkm")):
        assert db.one(pg, f"SELECT {db.SCHEMA}.same_component(%s, %s)", (a, b))[0] == keys.same_component(a, b), (a, b)
