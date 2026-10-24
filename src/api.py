"""OpenRouter, the key, and the spend ledger: the one place that holds a
credential, retries a request and counts money.

  api.key()                     the key from $WBG_OPENROUTER_KEY or .secrets/ (mode 0600)
  api.request(method, url, ..)  one HTTP request with retry on transient status codes
  api.post(path, body, key)     one OpenRouter call, parsed JSON
  api.Spend(conn, stage, set)   records calls and cost in the store, enforces --max-spend
"""
import os
import random
import stat
import time

import requests

from paths import main_checkout

KEY_VAR = "WBG_OPENROUTER_KEY"
KEY_FILE = os.path.join(".secrets", "wbg-openrouter-key")
BASE = "https://openrouter.ai/api"
HEADERS = {"HTTP-Referer": "https://github.com/devvartpoddar/wbg-digital-resilience",
           "X-Title": "wbg-digital-resilience",
           "User-Agent": "wbg-digital-resilience (research)"}
RETRY = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})


class Transport(RuntimeError):
    """A failure retrying may cure (throttling, a provider hiccup)."""


class Refused(RuntimeError):
    """A failure retrying cannot cure (bad key, bad model, bad payload)."""


class CapReached(SystemExit):
    """--max-spend would be passed; what was asked so far is stored."""


def key(required=False):
    """The credential from the environment, else from KEY_FILE in the main
    checkout, which must be readable by its owner alone. Never from an
    argument (visible in ps and in logs)."""
    k = os.environ.get(KEY_VAR, "").strip()
    path = os.path.join(main_checkout(), KEY_FILE)
    if not k and os.path.exists(path):
        if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            raise SystemExit(f"{path} is readable by others; chmod 600 it")
        with open(path, encoding="utf-8") as fh:
            k = fh.read().strip()
    if required and not k:
        raise SystemExit(f"no credential: set ${KEY_VAR} or write {path}")
    return k


def request(method, url, *, timeout=60, tries=5, retry=RETRY, headers=None, **kw):
    """One request, retried with backoff and jitter on connection errors and
    the status codes in `retry` (Retry-After honoured). Any other 4xx is
    Refused at once; the response is returned otherwise."""
    last = None
    for attempt in range(tries):
        try:
            r = requests.request(method, url, timeout=timeout, headers={**HEADERS, **(headers or {})},
                                 **kw)
        except requests.RequestException as exc:
            last = Transport(f"{type(exc).__name__}: {str(exc)[:300]}")
        else:
            if r.status_code in retry:
                last = Transport(f"HTTP {r.status_code}: {r.text[:200]}")
                wait = r.headers.get("Retry-After")
                if wait and wait.isdigit() and attempt < tries - 1:
                    time.sleep(min(int(wait), 120))
                    continue
            elif 400 <= r.status_code < 500:
                raise Refused(f"HTTP {r.status_code} from {url}: {r.text[:500]}")
            else:
                r.raise_for_status()
                return r
        if attempt < tries - 1:
            time.sleep(2 ** attempt + random.random())
    raise last


def post(path, body, api_key, timeout=60, tries=5):
    """One OpenRouter POST, as parsed JSON."""
    r = request("POST", BASE + path, json=body, timeout=timeout, tries=tries,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        return r.json()
    except ValueError:
        raise Transport(f"response was not JSON: {r.text[:200]}")


# ---------------------------------------------------------------- the ledger
#
# Every call that costs money goes through a Spend: it checks the cap before a
# step, adds what each call paid, and writes one ledger row when the step ends
# (in a finally, so a cap stop or a refusal still lands in the ledger). The cap
# is $WBG_SPEND_CAP_USD less what the ledger records since $WBG_SPEND_SINCE
# (both set by ./wbg run --max-spend) less what this process has paid and not
# yet recorded.

_unrecorded = [0.0]


def cap_left(conn):
    """US$ this process may still spend, or None with no cap."""
    cap = os.environ.get("WBG_SPEND_CAP_USD")
    if not cap:
        return None
    since = os.environ.get("WBG_SPEND_SINCE") or "1970-01-01"
    import db
    spent = db.one(conn, f"SELECT coalesce(sum(cost_usd), 0) FROM {db.STORE}.spend WHERE at >= %s",
                   (since,))[0]
    return float(cap) - float(spent) - _unrecorded[0]


class Spend:
    """The ledger of one step: with Spend(conn, stage, label_set, model) as s:
    s.check(estimate); ... s.add(cost) per call; the row is written on exit."""

    def __init__(self, conn, stage, label_set, model):
        self.conn, self.stage, self.label_set, self.model = conn, stage, label_set, model
        self.calls, self.cost, self.t0 = 0, 0.0, time.time()

    def check(self, estimate):
        left = cap_left(self.conn)
        if left is not None and estimate > left:
            raise CapReached(f"{self.stage}: --max-spend would be passed (about US${estimate:.3f} "
                             f"to ask, US${max(left, 0):.3f} left); nothing asked")

    def add(self, cost):
        self.calls += 1
        self.cost += cost
        _unrecorded[0] += cost
        left = cap_left(self.conn)
        if left is not None and left < 0:
            raise CapReached(f"{self.stage}: --max-spend reached after {self.calls:,} calls; "
                             f"the answers so far are stored")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.calls:
            import db
            db.execute(self.conn, f"INSERT INTO {db.STORE}.spend (at, stage, label_set, model, calls, "
                       f"cost_usd, seconds) VALUES (now(), %s, %s, %s, %s, %s, %s)",
                       (self.stage, self.label_set, self.model, self.calls, round(self.cost, 6),
                        round(time.time() - self.t0, 1)))
            self.conn.commit()
            _unrecorded[0] -= self.cost
        return False
