"""Every command that can pay for a model (it takes --max-spend) refuses to start against an empty
answer store under a schema that is not the default pair, unless --fresh-store says so (db.check_store).
The list of paid commands is read from the command line itself, so a new paid command is covered."""
import argparse

import pytest

from wbg_portfolio import api
from wbg_portfolio import cli

# how each paid command is started for the test (a dry run: nothing is asked either way)
START = {"run": ["run", "--only", "classify", "--dry-run"],
         "add": ["add", "cra", "--dry-run"],
         "try": ["try", "x", "--dry-run"],
         "check": ["check", "grade", "--dry-run"]}


def paid_commands():
    """The subcommands whose parser takes --max-spend."""
    seen = {}
    real = argparse.ArgumentParser.parse_args

    def grab(self, argv=None, namespace=None):
        sub = next(a for a in self._actions if isinstance(a, argparse._SubParsersAction))
        for name, p in sub.choices.items():
            seen[name] = any("--max-spend" in a.option_strings for a in p._actions)
        raise SystemExit(0)
    argparse.ArgumentParser.parse_args = grab
    try:
        with pytest.raises(SystemExit):
            cli.main(["status"])
    finally:
        argparse.ArgumentParser.parse_args = real
    return sorted(n for n, paid in seen.items() if paid)


def test_the_paid_commands_are_the_ones_listed():
    assert paid_commands() == sorted(START)


@pytest.mark.parametrize("name", sorted(START))
def test_each_paid_command_refuses_an_empty_store(name, pg, corpus, fake_model, monkeypatch, capsys):
    monkeypatch.setattr(api, "key", lambda required=False: "k")
    with pytest.raises(SystemExit) as e:
        cli.main(["--data", corpus, *START[name]])
    assert "holds no answers" in str(e.value) and "--fresh-store" in str(e.value), (name, str(e.value))
