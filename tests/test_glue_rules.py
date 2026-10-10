"""Word repair rules (src/glue.py) on an invented vocabulary, so they run
without data."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "src"))
import glue  # noqa: E402


def _vocab(counts, prose=None):
    v = glue.Vocab(counts)
    v.prose = glue.Vocab(prose or {})
    v.foreign = glue.Vocab(counts)
    return v


def test_a_word_two_common_words_were_fused_into_is_split():
    # 'unconsultant' recurs (a clipped cell repeats it) but both halves are far
    # more common.
    v = _vocab({"un": 5000, "consultant": 2500, "unconsultant": 150})
    assert glue.unfuse("Recrutement d'unconsultant", v)[0] == "Recrutement d'un consultant"


def test_a_word_the_prose_uses_whole_is_not_unfused():
    v = _vocab({"an": 5000, "other": 900, "another": 60}, prose={"another": 40})
    assert glue.unfuse("another phase", v)[0] == "another phase"


def test_two_content_words_are_not_unfused():
    # Neither half is a function word: 'unidos' stays.
    v = _vocab({"uni": 900, "dos": 900, "unidos": 10})
    assert glue.unfuse("Estados Unidos", v)[0] == "Estados Unidos"


def test_an_english_prefix_is_not_taken_for_a_fused_word():
    v = _vocab({"in": 9000, "eligibility": 300, "ineligibility": 20})
    assert glue.unfuse("ineligibility of firms", v)[0] == "ineligibility of firms"


def test_of_two_possible_joins_the_more_common_word_wins():
    # 'prior itaires ayant': 'prioritaires' (54) beats 'prioritairesayant' (3).
    v = _vocab({"prioritaires": 54, "prioritairesayant": 3, "ayant": 13, "prior": 1,
                "itaires": 0})
    assert glue.rejoin("filières prior itaires ayant un", v)[0] == \
        "filières prioritaires ayant un"
