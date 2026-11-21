"""The package texts' vectors in the link stage (src/wbg_portfolio/checks.py).

A text with no cached vector is embedded on a real run (a key must be to hand, or the
package would be scored against nothing); on a dry run it is reported and left as a zero
vector, so an estimate can be read without calling the model. Invented hashes and texts.
"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
from wbg_portfolio import checks  # noqa: E402
from wbg_portfolio import embed  # noqa: E402

PACKAGES = [{"package_id": "P1:ab-g-1", "text": "supply of radios",
             "sha": "a" * 64},
            {"package_id": "P1:cd-h-2", "text": "supply of masts",
             "sha": "b" * 64}]


def test_a_dry_run_reports_the_unembedded_texts_and_carries_on(tmp_path):
    said = []
    out = checks.package_vectors(str(tmp_path), PACKAGES, "", said.append, dry_run=True)
    assert out.shape == (len(PACKAGES), embed.DEFAULT_DIM)
    assert not out.any()                                    # no vector: zeros, not a crash
    assert said and "2 package description(s) are not embedded yet" in said[0]
    assert "dry run does not embed them" in said[0]


def test_a_real_run_with_no_key_stops_before_scoring_against_nothing(tmp_path):
    with pytest.raises(SystemExit) as caught:
        checks.package_vectors(str(tmp_path), PACKAGES, "", lambda line: None)
    assert "are not embedded yet and no key is set" in str(caught.value)


def test_a_cached_text_keeps_its_vector_on_a_dry_run(tmp_path):
    root = embed.cache_root(str(tmp_path), embed.DEFAULT_MODEL, embed.DEFAULT_DIM)
    blob = struct.pack("<f", 1.0) * embed.DEFAULT_DIM
    embed.write_cached(root, PACKAGES[0]["sha"], blob)
    said = []
    out = checks.package_vectors(str(tmp_path), PACKAGES, "", said.append, dry_run=True)
    assert out[0][0] == 1.0                                 # read from the cache
    assert not out[1].any()                                 # the one not embedded yet
    assert said and "1 package description(s)" in said[0]
