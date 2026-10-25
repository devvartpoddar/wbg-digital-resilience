"""The skip rule (docs/method/decisions.md): a label's gate is the highest
cosine that leaves at most skip.max_loss of the graded yes below it (a yes a
word pattern matches is always asked). Fitted by src/wbg_portfolio/grading.py from
skip.min_positives graded yes or more, stored through src/wbg_portfolio/fits.py; until
then the panel's starting gate applies.
"""
import math

from wbg_portfolio import panel


def settings():
    return {"max_loss": float(panel.get("skip.max_loss", 0.01)),
            "min_positives": int(panel.get("skip.min_positives", 10))}


def fit(positives, s=None):
    """The gate for one label from its graded yes [(best cosine, pattern
    matched, design weight)]: the highest cosine that leaves at most
    skip.max_loss of the yes weight below it, unmatched by a pattern.
    Returns (gate, weighted share lost, positives), or None with fewer than
    skip.min_positives yes, or when the yes the patterns miss weigh no more
    than the allowed loss (they then set no bound, and the panel's gate
    stands)."""
    s = dict(settings(), **(s or {}))
    if len(positives) < s["min_positives"]:
        return None
    total = sum(w for _, _, w in positives) or 1.0
    exposed = sorted((c, w) for c, hit, w in positives if not hit)
    if sum(w for _, w in exposed) / total <= s["max_loss"]:
        return None
    gate, lost, below = None, 0.0, 0.0
    for c, w in exposed:
        if below / total > s["max_loss"]:
            break
        gate, lost = c, below / total
        below += w
    return round(gate, 6), round(lost, 6), len(positives)


def loss_bound(lost, weights, conf=0.95):
    """How much of the graded yes the gate could lose, at most, given what
    the grades show: the one-sided Clopper-Pearson upper bound on the share
    lost, on the effective sample size of the yes (Kish). With n yes and
    none lost it is about 3/n (the rule of three), so 30 yes bound the loss
    at about 10 per cent and 300 at 1: a fitted gate meets skip.max_loss on
    the grades, and the bound says how far the grades can vouch for that."""
    s, s2 = sum(weights), sum(w * w for w in weights)
    n = round(s * s / s2) if s2 else 0
    if not n:
        return None
    k = min(n, round(lost * n))
    if k >= n:
        return 1.0
    cdf = lambda p: sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))
    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > 1 - conf else (lo, mid)
    return round(hi, 4)







