"""A run over named projects does not replace the cohort's rows in Postgres
(src/store.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import store  # noqa: E402


def test_a_run_over_named_projects_is_not_published(monkeypatch):
    said = []
    monkeypatch.delenv("WBG_PUBLISH_SCOPED", raising=False)
    monkeypatch.setattr(store, "connect", lambda: (_ for _ in ()).throw(AssertionError("connected")))
    assert store.publish("data", sets=["cra"], projects=["P1"], log=said.append) == {}
    assert "not written" in said[0]


def test_it_is_published_when_asked(monkeypatch):
    monkeypatch.setenv("WBG_PUBLISH_SCOPED", "1")
    monkeypatch.setattr(store, "connect", lambda: (_ for _ in ()).throw(RuntimeError("reached")))
    try:
        store.publish("data", sets=["cra"], projects=["P1"], log=lambda *_: None)
    except RuntimeError as exc:
        assert str(exc) == "reached"
    else:
        raise AssertionError("did not try to connect")
