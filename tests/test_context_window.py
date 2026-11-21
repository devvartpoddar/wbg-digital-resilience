"""The paragraph a context question sends is centred on the sentence and always holds it
(questions.window, Corpus.paragraph_window); the graders' marked paragraph uses the same window."""
import pytest

from wbg_portfolio import questions as Q
from wbg_portfolio import sampling


@pytest.mark.parametrize("length,start,end,chars", [
    (5000, 4900, 4990, 1800),        # the sentence at the end of a long paragraph
    (5000, 0, 80, 1800),             # at the start
    (5000, 2400, 2500, 1800),        # in the middle
    (900, 400, 500, 1800),           # a paragraph shorter than the window
    (5000, 1000, 3500, 1800),        # a sentence longer than the window
])
def test_the_window_holds_the_sentence_and_is_centred(length, start, end, chars):
    lo, hi = Q.window(length, start, end, chars)
    assert lo <= start and end <= hi <= length
    assert hi - lo == max(min(chars, length), end - start)
    if chars <= length and end - start < chars and chars // 2 < start < length - chars // 2:
        assert abs((start - lo) - (hi - end)) <= 1


def test_the_context_field_of_a_long_paragraph_holds_its_last_sentence(corpus, monkeypatch):
    c = Q.Corpus(corpus)
    u = next(r["unit_id"] for r in c.index if c.texts[r["unit_id"]].endswith("The road will be widened."))
    text = c.paragraph_text(c.row(u)["paragraph_id"])
    monkeypatch.setattr(Q.panel, "require", lambda key: 20 if key == "second.context_chars" else None)
    ctx = c.context_state(u)["context"]
    assert "The road will be widened." in ctx and len(ctx) == 25      # the paragraph's last sentence
    assert not text.startswith(ctx)                   # not the paragraph's first characters


def test_the_graders_mark_uses_the_same_window():
    p = "x" * 6000 + " The sentence. " + "y" * 100
    out = sampling.mark(p, "The sentence.")
    assert ">>> The sentence. <<<" in out and len(out) <= sampling.PARAGRAPH_CHARS + 8
