"""Put back the spaces the procurement plan renditions lost, and remove the ones
they inserted.

A cell clipped at the column edge runs two words together ('ofDigital'). A token
is split only when it is not itself a corpus word and both halves are (a
two-letter half only if a function word, never a common ending, never a short
acronym); the cut with the most common rarer half wins. A case boundary is tried
first, and a mixed-case name the appraisal prose uses whole stays. rejoin()
repairs the opposite defect ('S upply'). The vocabulary is the descriptions plus
the appraisal prose; only spaces are ever changed.
"""
import re
from collections import Counter

MIN_SEEN = 3
# Without English prose to arbitrate, frequency does: a joined or split form wins only when
# it is this many times more common than the alternative.
RATIO = 5
MIN_HALF = 2
PROSE_MIN_COUNT = 2     # the prose (or its names) uses a word whole this often to count it
ACRONYM_MAX_CHARS = 6   # an all-capitals token this short is an acronym, never split
CAMEL_MIN_HALF = 4      # both halves this long let a known right half vouch for the left
SPLIT_MIN_PART = 3      # a frequency split leaves at least this many letters each side
SHORT_HALF_CHARS = 3    # a half this short makes a whole token more likely a foreign word...
COMMON_SHORT_WORD = 50  # ...unless the prose uses that half at least this often
FUSED_MIN_PART = 2      # a fused pair is cut with at least this many letters each side
FUSED_RIGHT_MIN = 4     # a function word on the right of a fused pair is this long
UNFUSE_MIN_CHARS = 5    # a shorter word is never unfused
REJOIN_MAX_PARTS = 3    # rejoin glues up to this many neighbouring fragments
# A two-letter half is only believed when it is a function word: the corpus is full of 'es',
# 'er', 'us' as fragments of longer words.
SHORT_WORDS = {"of", "to", "in", "on", "at", "an", "by", "or", "as", "is", "it",
               "de", "du", "la", "le", "et", "en", "au", "un", "da", "do", "em",
               "no", "na", "os", "el", "al", "se", "il", "ou", "ao", "e"}
# Endings that are also short words somewhere.
ENDINGS = {"es", "er", "ers", "eur", "eurs", "ies", "us", "one", "res", "ops",
           "ing", "ed", "ion", "ions", "ment", "ments", "ors", "ity", "al",
           "ly", "ive", "ure", "age", "ance", "ence", "able", "ible", "ist",
           "ism", "ize", "ise", "ent", "ant", "ique", "iques", "ation", "ations",
           "tion", "tions", "ness", "ship", "ful", "less", "ous", "ic", "ics",
           # Spanish and Portuguese plurals: 'formatos' is not 'format os'.
           "os", "as", "ões", "ción", "ação"}
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Words after which a capitalised token is a new word even when the corpus has not seen it:
# 'andPemba', 'forZanzibar'.
FUNCTION_WORDS = {"and", "for", "the", "of", "with", "to", "in", "at", "on", "by",
                  "from", "de", "du", "des", "la", "le", "les", "et", "pour",
                  "dans", "au", "aux", "en", "do", "da", "dos", "das", "para",
                  "e", "y", "el", "los", "las", "del", "con"}
# What a clipped cell runs onto the next word: 'unconsultant', 'laconstruction'.
FUSED_LEFT = (SHORT_WORDS | FUNCTION_WORDS | {"une", "sur", "par"}) - {"in"}
EN_FUNCTION = {"and", "for", "the", "of", "with", "to", "in", "at", "on", "by", "from"}
EN_SHORT = {"of", "to", "in", "on", "at", "an", "by", "or", "as", "is", "it", "we",
            "be", "he", "if", "so", "no", "up", "us", "do", "go", "my", "me", "am"}
ROMAN = re.compile(r"^(?:I{1,3}|IV|V|VI{0,3}|IX|X)$")


class Vocab(Counter):
    """Word counts, with the appraisal prose kept apart as `prose`."""
    prose = None


def corpus_vocab(descriptions, data=None):
    """(vocab, names) for a run: words from the descriptions plus the cleaned
    appraisal text in data/appraisal/text/ when it is there, and the mixed-case names
    that prose uses whole ('GovNet')."""
    import glob
    from paths import where
    # Each distinct description counted once: a package is re-printed in every plan version,
    # up to dozens of times, and counting every copy lets one repeated glue
    # ('consultantindividuel', 1,035 copies) outvote real words.
    vocab = Vocab(build_vocab(sorted(set(descriptions))))
    # French, Portuguese and Spanish are judged against the descriptions alone: the
    # appraisal prose is English and its counts would vouch for English fragments inside a
    # foreign description.
    vocab.foreign = Vocab(vocab)
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
    # The prose is English, so short words are judged as English: 'al', 'y' and 'e' are
    # words in Spanish and Portuguese but fragments in 'Digit al', 'Cybersecurit y', 'pilot
    # e'.
    if len(w) == 1:
        return w in ("a", "i")
    if len(w) == 2:
        return w in EN_SHORT
    return prose.get(w, 0) >= PROSE_MIN_COUNT


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
    """Where to put a space in this token, or None."""
    if token.isupper() and len(token) <= ACRONYM_MAX_CHARS:
        return None                         # an acronym: UNOPS, NIDC
    boundaries = [m.start() for m in re.finditer(r"(?<=[a-zà-ÿ])(?=[A-ZÀ-Þ])", token)]
    if boundaries:
        if names is not None and names.get(token.lower(), 0) >= PROSE_MIN_COUNT:
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
                (len(left) >= CAMEL_MIN_HALF and len(right) >= CAMEL_MIN_HALF
                 and _known(right, vocab))
            if left_ok and (_known(right, vocab) or right.isupper()):
                return b
    if len(token) < 2 * MIN_HALF:
        return None
    if _prose_word(token, vocab):
        return None                         # the prose uses it whole
    if _prose_word(token, vocab) is None and _known(token, vocab):
        # No prose: a repeated glue is still a glue when both halves are far more common
        # than the glued form - 'informatiquepour'.
        tc = vocab.get(token.lower(), 0)
        best, score = None, 0
        for i in range(SPLIT_MIN_PART, len(token) - 1):
            left, right = token[:i], token[i:]
            if len(right) < SPLIT_MIN_PART and right.lower() not in SHORT_WORDS:
                continue
            if right.lower() in ENDINGS:
                continue
            lc, rc = vocab.get(left.lower(), 0), vocab.get(right.lower(), 0)
            if min(lc, rc) >= RATIO * tc and min(lc, rc) > score:
                best, score = i, min(lc, rc)
        return best
    # The token is not a prose word.
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
        # A word the descriptions use whole, with a half of three letters or fewer, is far
        # more likely a foreign word than a glue - 'formatos' is not 'format os', 'contable'
        # not 'con table' - unless the short half is a word English prose uses all the time,
        # as in 'Provisionof'.
        short = left if len(left) <= SHORT_HALF_CHARS else \
            right if len(right) <= SHORT_HALF_CHARS else None
        if ok and short and _known(token, vocab):
            prose = getattr(vocab, "prose", None) or {}
            if prose.get(short.lower(), 0) < COMMON_SHORT_WORD:
                continue
        # A token that also occurs whole is split only when both halves are far more common
        # than it: 'designingand' yes, 'scanner' (scan + a stray 'ner') no.
        tc = vocab.get(token.lower(), 0)
        if ok and tc and min(vocab.get(left.lower(), 0),
                             vocab.get(right.lower(), 0)) < RATIO * tc:
            ok = False
        if ok:
            s = min(vocab[left.lower()], vocab[right.lower()])
            if s > score:
                best, score = i, s
    return best


def _fused_cut(word, vocab):
    """Where to split a word that is two common words run together - 'uncadre'
    (un + cadre), 'unconsultant', 'laconstruction' - or None."""
    w = word.lower()
    wc = vocab.get(w, 0)
    best, score = None, 0
    for i in range(FUSED_MIN_PART, len(w) - FUSED_MIN_PART):
        left, right = w[:i], w[i:]
        if right in ENDINGS:
            continue
        if not (left in FUSED_LEFT or (right in FUNCTION_WORDS and len(right) >= FUSED_RIGHT_MIN)):
            continue
        s = min(vocab.get(left, 0), vocab.get(right, 0))
        if s >= max(RATIO * wc, MIN_SEEN) and s > score:
            best, score = i, s
    return best


def unfuse(text, vocab, prose_vocab=None):
    """Split the fused pairs that survive the other passes ('d'unconsultant'
    -> "d'un consultant"), except a word the appraisal prose uses whole
    ('another', 'information'). The prose is asked even for a French or
    Spanish description, since English words occur in them ('[Insert name]').
    Returns (text, splits)."""
    n = 0
    prose_vocab = prose_vocab if prose_vocab is not None else vocab

    def fix(m):
        nonlocal n
        tok = m.group(0)
        if len(tok) < UNFUSE_MIN_CHARS or _prose_word(tok, prose_vocab):
            return tok
        cut = _fused_cut(tok, vocab)
        if cut is None:
            return tok
        n += 1
        return tok[:cut] + " " + tok[cut:]

    return WORD.sub(fix, text), n


def rejoin(text, vocab):
    """The opposite defect, a space inside a word ('S upply'): up to
    REJOIN_MAX_PARTS fragments are joined when the whole is a corpus word and
    they are not all words in their own right ('in formation' stays)."""
    toks = text.split(" ")
    out, n, i = [], 0, 0
    while i < len(toks):
        joined = False
        # Every join that passes is a candidate; the one making the more common word wins:
        # 'prior itaires ayant' is 'prioritaires ayant' (54 uses), not 'prioritairesayant'
        # (3).
        candidates = []
        for k in range(REJOIN_MAX_PARTS, 1, -1):
            if i + k > len(toks):
                continue
            parts = toks[i:i + k]
            # Letters only at the seams: the first part may carry punctuation before its
            # word, the last part after it, nothing in between.
            if not all(re.fullmatch(r"[^\W\d_]+", p) for p in parts[1:-1]):
                continue
            ma = re.search(r"([^\W\d_]+)$", parts[0])
            mb = re.match(r"^([^\W\d_]+)", parts[-1])
            if not (ma and mb):
                continue
            # An elided article belongs to the NEXT word: "d'un", "l'appui".
            if parts[-1][mb.end():mb.end() + 1] in ("'", "\u2019") and \
                    mb.group(1).lower() in ("d", "l", "qu", "j", "n", "s", "c", "m", "t"):
                continue
            # A code ends in its own letters: 'ID4D Project' stays two.
            if ma.start() > 0 and parts[0][ma.start() - 1].isalnum():
                continue
            # The first piece of a hyphenated word stays its own: 'for e-commerce' is not
            # 'fore-commerce'.
            if parts[-1][mb.end():mb.end() + 1] == "-":
                continue
            words = [ma.group(1)] + parts[1:-1] + [mb.group(1)]
            # A capital opening a later fragment starts a new word ('TV And').
            if any(w[:1].isupper() and not words[0].isupper() for w in words[1:]):
                continue
            whole = "".join(words)
            verdicts = [_prose_word(w, vocab) for w in words]
            # An acronym before a lower-case word is two words: 'I T equipment' is 'IT
            # equipment', never 'ITequipment'.
            head = "".join(words[:-1])
            if head.isupper() and len(head) >= 2 and words[-1].islower() \
                    and len(words[-1]) >= 3 and verdicts[-1]:
                continue
            if all(v is None for v in verdicts):
                # No prose: a fragment is a single letter that is not a word, or a part far
                # rarer than the whole it would make.
                wc = vocab.get(whole.lower(), 0)
                all_words = all(
                    (len(w) > 1 or w.lower() in ("a", "y", "e", "o", "à"))
                    and _known(w, vocab) and vocab.get(w.lower(), 0) * RATIO > wc
                    for w in words)
            else:
                all_words = all(bool(v) for v in verdicts)
            # A function word at either end stays its own word: 'Pr ovision of' becomes
            # 'Provision of', not 'Provisionof'.
            fw = EN_FUNCTION if getattr(vocab, "prose", None) is not None else FUNCTION_WORDS
            if words[-1].lower() in fw and _stands("".join(words[:-1]), vocab):
                continue
            if len(words[0]) > 1 and words[0].lower() in fw and \
                    _stands("".join(words[1:]), vocab):
                continue
            # Three letters is enough for a function word ('a nd') or for a word made only
            # of one- and two-letter shards ('E-G ov').
            short_ok = whole.lower() in fw or (
                len(whole) == 3 and all(len(w) <= 2 for w in words)
                and not any(verdicts))
            if (len(whole) >= 4 or short_ok) and not all_words and \
                    vocab.get(whole.lower(), 0) >= MIN_SEEN:
                candidates.append((vocab.get(whole.lower(), 0), k,
                                   parts[0] + "".join(parts[1:])))
        if candidates:
            _count, k, merged = max(candidates)
            out.append(merged)
            n += k - 1
            i += k
            joined = True
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
    """(repaired text, number of spaces inserted or removed)."""
    if not text:
        return text, 0
    full = vocab
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
    # A closing bracket or a comma run straight into the next word: '(TPC)Buildings',
    # 'Equipment,Servers'.
    out, k = re.subn(r"(?<=[)\],;])(?=[^\W\d_])", " ", out)
    out, j = re.subn(r"(?<=[^\W\d_]{2})\((?=[^\W\d_])", " (", out)
    # A split can leave a fragment that only now has a neighbour to join ('Consult
    # ancyServices' -> 'Consult ancy Services'), or a space one letter out ('forthe' ->
    # 'fort he'), so the join pass runs once more.
    out, m = rejoin(out, vocab)
    # Last, two common words a clipped cell ran together, which the passes above can leave
    # whole because the fused form recurs ('unconsultant').
    out, f = unfuse(out, vocab, full)
    return out, n + k + j + m + f
