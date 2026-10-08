"""Put back the spaces the procurement plan renditions lost.

The text rendition of a STEP plan clips each table cell at the column edge, and
the parser has to rejoin the fragments. Where the clip fell inside a word the
halves belong together ('equipm' + 'ent'); where it fell on a space, the space
is gone and two words run together ('Design and Build of' + 'Digital' ->
'ofDigital'). The rendition does not say which, so the join cannot decide.

This decides afterwards, from the words the corpus itself uses. A token is
split in two only when all of these hold:

  - the token is not itself a word the corpus uses on its own
    ('database' stays 'database' however often 'data' and 'base' occur)
  - both halves are words the corpus uses on their own
  - each half is at least two letters, and a two-letter half must be a
    function word ('of', 'de', 'la'), so 'ZoneI' is not cut into 'Zone I'
  - outside a case boundary, the right half is not a common ending
    ('Auditeur' is not 'Audit eur', 'parties' is not 'part ies')
  - short all-capital tokens are acronyms and are left whole

and where more than one cut qualifies, the one whose rarer half is most common
wins. A lowercase-to-uppercase boundary ('andRadio') is tried first, because
that is where the clip almost always lands. There the test is different: a
mixed-case token the appraisal prose uses whole ('GovNet', 'WiFi') is a name
and stays, and a clipped stub before a known word ('technologProcurement') is
still separated from it, since a stub glued to the next field is worse than a
stub on its own.

The vocabulary is built from the descriptions themselves, plus the cleaned
appraisal text when it is on disk: a word counts as known when it appears as a
whole token at least MIN_SEEN times. Nothing is looked up in a dictionary
that the corpus does not back, so French, Portuguese and Spanish descriptions
are handled by the same rule as English ones.

The opposite defect is repaired too (`rejoin`): a space put INSIDE a word,
'S upply', 'Ca pacity', where a collapsed layout broke a cell mid-word.

The original string is always kept beside the repaired one; this only ever
inserts or removes a space, never changes a character.
"""
import re
from collections import Counter

MIN_SEEN = 3
# Without English prose to arbitrate, frequency does: a joined or split form
# wins only when it is this many times more common than the alternative.
RATIO = 5
MIN_HALF = 2
# A two-letter half is only believed when it is a function word: the corpus
# is full of 'es', 'er', 'us' as fragments of longer words.
SHORT_WORDS = {"of", "to", "in", "on", "at", "an", "by", "or", "as", "is", "it",
               "de", "du", "la", "le", "et", "en", "au", "un", "da", "do", "em",
               "no", "na", "os", "el", "al", "se", "il", "ou", "ao", "e"}
# Endings that are also short words somewhere. Cut off a longer word they
# produce 'Audit eur', 'process us', 'part ies', 'dr one' - so a right half
# that is one of these never justifies a cut without a case boundary.
ENDINGS = {"es", "er", "ers", "eur", "eurs", "ies", "us", "one", "res", "ops",
           "ing", "ed", "ion", "ions", "ment", "ments", "ors", "ity", "al",
           "ly", "ive", "ure", "age", "ance", "ence", "able", "ible", "ist",
           "ism", "ize", "ise", "ent", "ant", "ique", "iques", "ation", "ations",
           "tion", "tions", "ness", "ship", "ful", "less", "ous", "ic", "ics",
           # Spanish and Portuguese plurals: 'formatos' is not 'format os'.
           "os", "as", "ões", "ción", "ción", "ação"}
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Words after which a capitalised token is a new word even when the corpus has
# not seen it: 'andPemba', 'forZanzibar'.
FUNCTION_WORDS = {"and", "for", "the", "of", "with", "to", "in", "at", "on", "by",
                  "from", "de", "du", "des", "la", "le", "les", "et", "pour",
                  "dans", "au", "aux", "en", "do", "da", "dos", "das", "para",
                  "e", "y", "el", "los", "las", "del", "con"}
EN_FUNCTION = {"and", "for", "the", "of", "with", "to", "in", "at", "on", "by", "from"}
EN_SHORT = {"of", "to", "in", "on", "at", "an", "by", "or", "as", "is", "it", "we",
            "be", "he", "if", "so", "no", "up", "us", "do", "go", "my", "me", "am"}
ROMAN = re.compile(r"^(?:I{1,3}|IV|V|VI{0,3}|IX|X)$")


class Vocab(Counter):
    """Word counts, with the appraisal prose kept apart as `prose`.

    The descriptions cannot be the sole judge of what is a word: they carry
    the very defects being repaired, so 'missioning' (from 'Com missioning')
    and 'tvand' (from 'TVand') occur often enough there to look like words.
    The appraisal text, read from PDFs, has neither, so where it has an opinion
    it decides; the descriptions decide only for words it never uses, which is
    most French, Portuguese and Spanish."""
    prose = None


def corpus_vocab(descriptions, data=None):
    """(vocab, names) for a run: words from the descriptions plus the cleaned
    appraisal text in data/appraisal/text/ when it is there, and the mixed-case names
    that prose uses whole ('GovNet')."""
    import glob
    from paths import where
    # Each distinct description counted once: a package is re-printed in every
    # plan version, up to dozens of times, and counting every copy lets one
    # repeated glue ('consultantindividuel', 1,035 copies) outvote real words.
    vocab = Vocab(build_vocab(sorted(set(descriptions))))
    names = None
    if data:
        paths = sorted(glob.glob(where(data, "text", "*.txt")))
        if paths:
            names, vocab.prose = Counter(), Counter()
        for path in paths:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for w in WORD.findall(text):
                vocab[w.lower()] += 1
                vocab.prose[w.lower()] += 1
                if re.search(r"[a-z][A-Z]", w):
                    names[w.lower()] += 1
    vocab.foreign = Vocab(vocab)        # same counts, no prose: for non-English
    return vocab, names


def _prose_word(word, vocab):
    """True / False when the appraisal prose has an opinion, None when it has
    none (no prose loaded, or a word it never uses)."""
    prose = getattr(vocab, "prose", None)
    if prose is None:
        return None
    if len(word) >= 2 and word.isupper():
        return True                         # an acronym: TV, ICT
    w = word.lower()
    # The prose is English, so short words are judged as English: 'al', 'y'
    # and 'e' are words in Spanish and Portuguese but fragments in 'Digit al',
    # 'Cybersecurit y', 'pilot e'.
    if len(w) == 1:
        return w in ("a", "i")
    if len(w) == 2:
        return w in EN_SHORT
    return prose.get(w, 0) >= 2


def build_vocab(texts):
    """Lowercased word -> how often it appears as a whole token."""
    c = Counter()
    for t in texts:
        for w in WORD.findall(t or ""):
            c[w.lower()] += 1
    return c


def _known(word, vocab):
    w = word.lower()
    if len(w) == 2 and w not in SHORT_WORDS:
        return False
    return vocab.get(w, 0) >= MIN_SEEN


def _best_cut(token, vocab, names=None):
    """Where to put a space in this token, or None.

    `names` is the vocabulary of the appraisal prose. A mixed-case token such
    as 'GovNet', 'MInT' or 'WiFi' that the prose uses whole is a name and is
    left alone; 'DataCenter' is not one the prose uses, so it is split even
    though the plans repeat it often enough to look like a word."""
    if token.isupper() and len(token) <= 6:
        return None                         # an acronym: UNOPS, NIDC
    boundaries = [m.start() for m in re.finditer(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", token)]
    if boundaries:
        if names is not None and names.get(token.lower(), 0) >= 2:
            return None
        if names is None and _known(token, vocab):
            return None
        for b in boundaries:
            left, right = token[:b], token[b:]
            if left.lower() in FUNCTION_WORDS:
                return b                    # 'andPemba', 'forZanzibar'
            if ROMAN.match(right) and _known(left, vocab):
                return b                    # 'ZoneV'
            if len(right) < MIN_HALF:
                continue
            # 'aConsultancy': the article is the one single letter allowed.
            left_ok = _known(left, vocab) or left.lower() == "a" or \
                (len(left) >= 4 and len(right) >= 4 and _known(right, vocab))
            if left_ok and (_known(right, vocab) or right.isupper()):
                return b
    if len(token) < 2 * MIN_HALF:
        return None
    if _prose_word(token, vocab):
        return None                         # the prose uses it whole
    if _prose_word(token, vocab) is None and _known(token, vocab):
        # No prose: a repeated glue is still a glue when both halves are far
        # more common than the glued form - 'informatiquepour'.
        tc = vocab.get(token.lower(), 0)
        best, score = None, 0
        for i in range(3, len(token) - 1):
            left, right = token[:i], token[i:]
            if len(right) < 3 and right.lower() not in SHORT_WORDS:
                continue
            if right.lower() in ENDINGS:
                continue
            lc, rc = vocab.get(left.lower(), 0), vocab.get(right.lower(), 0)
            if min(lc, rc) >= RATIO * tc and min(lc, rc) > score:
                best, score = i, min(lc, rc)
        return best
    # The token is not a prose word. If the descriptions repeat it, it may be a
    # foreign word - unless the prose knows it as two English words glued,
    # which is the defect itself repeated ('designingand').
    best, score = None, 0
    for i in range(MIN_HALF, len(token) - MIN_HALF + 1):
        left, right = token[:i], token[i:]
        if right.lower() in ENDINGS:
            continue
        pl, pr = _prose_word(left, vocab), _prose_word(right, vocab)
        if pl is not None or pr is not None:
            ok = bool(pl) and bool(pr)
        else:
            ok = _known(left, vocab) and _known(right, vocab)
        # A word the descriptions use whole, with a two-letter half, is far
        # more likely a foreign word than a glue - 'formatos' is not 'format
        # os' - unless the short half is a word English prose uses all the
        # time, as in 'Provisionof'.
        short = left if len(left) < 3 else right if len(right) < 3 else None
        if ok and short and _known(token, vocab):
            prose = getattr(vocab, "prose", None) or {}
            if prose.get(short.lower(), 0) < 50:
                continue
        # A token that also occurs whole is split only when both halves are
        # far more common than it: 'designingand' yes, 'scanner' (scan + a
        # stray 'ner') no.
        tc = vocab.get(token.lower(), 0)
        if ok and tc and min(vocab.get(left.lower(), 0),
                             vocab.get(right.lower(), 0)) < RATIO * tc:
            ok = False
        if ok:
            s = min(vocab[left.lower()], vocab[right.lower()])
            if s > score:
                best, score = i, s
    return best


def rejoin(text, vocab):
    """The opposite defect: a space inserted INSIDE a word by a layout that
    broke the cell mid-word - 'S upply', 'Com missioning', 'Especi alist as'.
    Two or three neighbouring fragments are joined when the joined form is a
    word the corpus uses and they are not all words in their own right. 'in
    formation' stays as written, since both halves are words."""
    toks = text.split(" ")
    out, n, i = [], 0, 0
    while i < len(toks):
        joined = False
        for k in (3, 2):
            if i + k > len(toks):
                continue
            parts = toks[i:i + k]
            # Letters only at the seams: the first part may carry punctuation
            # before its word, the last part after it, nothing in between.
            if not all(re.fullmatch(r"[^\W\d_]+", p) for p in parts[1:-1]):
                continue
            ma = re.search(r"([^\W\d_]+)$", parts[0])
            mb = re.match(r"^([^\W\d_]+)", parts[-1])
            if not (ma and mb):
                continue
            # An elided article belongs to the NEXT word: "d'un", "l'appui".
            # A possessive does not: "Gover nor's" is one word.
            if parts[-1][mb.end():mb.end() + 1] in ("'", "\u2019") and \
                    mb.group(1).lower() in ("d", "l", "qu", "j", "n", "s", "c", "m", "t"):
                continue
            words = [ma.group(1)] + parts[1:-1] + [mb.group(1)]
            # A capital opening a later fragment starts a new word ('TV And').
            if any(w[:1].isupper() and not words[0].isupper() for w in words[1:]):
                continue
            whole = "".join(words)
            verdicts = [_prose_word(w, vocab) for w in words]
            # An acronym before a lower-case word is two words: 'I T equipment'
            # is 'IT equipment', never 'ITequipment'.
            if words[0].isupper() and words[-1].islower() and len(words[-1]) >= 3 \
                    and verdicts[-1]:
                continue
            if all(v is None for v in verdicts):
                # No prose: a fragment is a single letter that is not a word,
                # or a part far rarer than the whole it would make.
                wc = vocab.get(whole.lower(), 0)
                all_words = all(
                    (len(w) > 1 or w.lower() in ("a", "y", "e", "o", "à"))
                    and _known(w, vocab) and vocab.get(w.lower(), 0) * RATIO > wc
                    for w in words)
            else:
                all_words = all(bool(v) for v in verdicts)
            # A function word at either end stays its own word: 'Pr ovision
            # of' becomes 'Provision of', not 'Provisionof'.
            # Only when the rest already stands as a word: 'Commissi on' is
            # one broken word, and 'an d' is 'and'.
            fw = EN_FUNCTION if getattr(vocab, "prose", None) is not None else FUNCTION_WORDS
            if words[-1].lower() in fw and _stands("".join(words[:-1]), vocab):
                continue
            if len(words[0]) > 1 and words[0].lower() in fw and \
                    _stands("".join(words[1:]), vocab):
                continue
            # Three letters is enough for a function word ('a nd') or for a
            # word made only of one- and two-letter shards ('E-G ov').
            short_ok = whole.lower() in fw or (
                len(whole) == 3 and all(len(w) <= 2 for w in words)
                and not any(verdicts))
            if (len(whole) >= 4 or short_ok) and not all_words and \
                    vocab.get(whole.lower(), 0) >= MIN_SEEN:
                out.append(parts[0] + "".join(parts[1:]))
                n += k - 1
                i += k
                joined = True
                break
        if not joined and i + 1 < len(toks) and _moved_space(toks[i], toks[i + 1], vocab):
            out.extend(_moved_space(toks[i], toks[i + 1], vocab))
            n += 1
            i += 2
            joined = True
        if not joined:
            out.append(toks[i])
            i += 1
    return " ".join(out), n


def _stands(word, vocab):
    """Is this a word in its own right? The prose decides where it has a view;
    the descriptions' own counts only where it has none, because a word broken
    often enough ('Commissi') is common in the descriptions themselves."""
    v = _prose_word(word, vocab)
    return _known(word, vocab) if v is None else bool(v)


def _moved_space(a, b, vocab):
    """A space one letter out of place: 'forth e' is 'for the'. Only when the
    second part is not a word, the first new word is a function word and the
    second is a word the prose uses."""
    if getattr(vocab, "prose", None) is None or not re.fullmatch(r"[a-z]+", a + b):
        return None
    if _prose_word(b, vocab) or len(b) > 2:
        return None
    whole = a + b
    for cut in range(1, len(whole)):
        x, y = whole[:cut], whole[cut:]
        if x != a and x in EN_FUNCTION and len(y) >= 2 and _prose_word(y, vocab):
            return [x, y]
    return None


def repair(text, vocab, names=None, lang="en"):
    """(repaired text, number of spaces inserted or removed).

    The appraisal prose is English, so it only arbitrates English text; for a
    French, Portuguese or Spanish description its silence on a word means
    nothing, and the descriptions' own vocabulary decides alone."""
    if not text:
        return text, 0
    if lang != "en":
        vocab = getattr(vocab, "foreign", vocab)
    text, n = rejoin(text, vocab)

    def fix(m):
        nonlocal n
        tok = m.group(0)
        cut = _best_cut(tok, vocab, names)
        if cut is None:
            return tok
        n += 1
        return tok[:cut] + " " + tok[cut:]

    out = WORD.sub(fix, text)
    # A closing bracket or a comma run straight into the next word:
    # '(TPC)Buildings', 'Equipment,Servers'.
    out, k = re.subn(r"(?<=[)\],;])(?=[^\W\d_])", " ", out)
    out, j = re.subn(r"(?<=[^\W\d_]{2})\((?=[^\W\d_])", " (", out)
    return out, n + k + j
