"""Join keys shared by more than one stage.

Kept apart from text_rules.py on purpose: text_rules is part of the appraisal
parser's version, so a change there re-reads every PDF. A join key is not."""
import re
import unicodedata

_COMPONENT_PREFIX = re.compile(
    r"^(?:Component|Sub-?component)\s*\d+(?:\.\d+)?\s*[.:\-–]?\s*", re.I)


def name_key(name):
    """The key two printings of one component name share: its "Component N:"
    prefix dropped, accents, case and everything but letters and digits removed.
    'Sub-component 2.1: Data Centre' and 'Data centre' give 'datacentre'.
    components.csv carries it, and procurement matches component names on it."""
    text = _COMPONENT_PREFIX.sub("", " ".join((name or "").split()))
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", text.casefold())
