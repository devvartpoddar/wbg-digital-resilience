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

from wbg_portfolio.paths import main_checkout

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
    """A failure retrying cannot cure (bad key, bad model, bad payload).

    status is the HTTP status; items says what the refused request was about (set by the asker that
    knows: a request about one item only). A refusal may be skipped as "this item" only when it
    is about one item, is not a key or payment failure, and the run has had an answer from the same
    model (so the key and the model work); otherwise it stops the run (fatal)."""

    def __init__(self, message, status=None, items=()):
        super().__init__(message)
        self.status, self.items = status, list(items)

    @property
    def hard(self):
        """True when it cannot be about one item: a bad key, no credit, or a status we do not know."""
        return self.status is None or self.status in FATAL_STATUS

    @property
    def fatal(self):
        """True when the run must stop at once: hard, or not known to be about one item."""
        return self.hard or not self.items


FATAL_STATUS = frozenset({401, 402})


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
                raise Refused(f"HTTP {r.status_code} from {url}: {r.text[:500]}", status=r.status_code)
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
# step and adds what each call paid. Its ledger row is written at the first call
# and brought up to date (and committed) at every call, so a kill loses at most
# the call in flight from the ledger, as from the answers, and a restarted run's
# cap sees what the killed one spent. The cap is $WBG_SPEND_CAP_USD less what the
# ledger records since $WBG_SPEND_SINCE (both set by ./wbg_portfolio run
# --max-spend, the window kept across restarts by spend_window).

import contextlib
import datetime
import json


def cap_left(conn):
    """US$ this process may still spend, or None with no cap."""
    cap = os.environ.get("WBG_SPEND_CAP_USD")
    if not cap:
        return None
    since = os.environ.get("WBG_SPEND_SINCE") or "1970-01-01"
    from wbg_portfolio import db
    spent = db.one(conn, f"SELECT coalesce(sum(cost_usd), 0) FROM {db.STORE}.spend WHERE at >= %s",
                   (since,))[0]
    return float(cap) - float(spent)


MEASURED_MIN_CALLS = 20        # paid requests the ledger needs before their average is the estimate


def measured_cost(conn, stage, model):
    """(US$ a request, basis): the ledger's average cost per request of this step with this model, else of
    the model over every step, once it holds MEASURED_MIN_CALLS paid requests (rows recorded at
    US$0 are left out: calls paid through the owner's own provider key were once recorded so); (None, "upper bound")
    with neither. What a step's estimate rests on is logged with it."""
    from wbg_portfolio import db
    if not db.exists(conn, f"{db.STORE}.spend"):
        return None, "upper bound (no measured cost yet)"
    for cond, args, what in (("stage = %s AND model = %s", (stage, model), f"step {stage}"),
                             ("model = %s", (model,), f"model {model}")):
        n, cost = db.one(conn, f"SELECT coalesce(sum(calls), 0), coalesce(sum(cost_usd), 0) "
                               f"FROM {db.STORE}.spend WHERE {cond} AND cost_usd > 0", args)
        conn.commit()
        if n >= MEASURED_MIN_CALLS and cost > 0:
            return float(cost) / n, f"measured: US${float(cost) / n:.6f} a request over {int(n):,} paid requests of {what}"
    return None, "upper bound (no measured cost yet)"


class Spend:
    """The ledger of one step: with Spend(conn, stage, label_set, model) as s:
    s.check(estimate); ... s.add(cost) per call. `lock` is the lock the step's
    workers hold while they write to the same connection: the ledger is written
    under it, so it never lands inside a worker's transaction."""

    def __init__(self, conn, stage, label_set, model, lock=None):
        self.conn, self.stage, self.label_set, self.model = conn, stage, label_set, model
        self.lock = lock or contextlib.nullcontext()
        self.calls, self.cost, self.t0, self.at = 0, 0.0, time.time(), None

    def check(self, estimate, basis=""):
        with self.lock:
            left = cap_left(self.conn)
        if left is not None and estimate > left:
            raise CapReached(f"{self.stage}: --max-spend would be passed (about US${estimate:.3f} "
                             f"to ask{', ' + basis if basis else ''}; US${max(left, 0):.3f} left); nothing asked")

    def _write(self):
        from wbg_portfolio import db
        values = (self.calls, round(self.cost, 6), round(time.time() - self.t0, 1))
        if self.at is None:
            self.at = datetime.datetime.now(datetime.timezone.utc)
            db.execute(self.conn, f"INSERT INTO {db.STORE}.spend (at, stage, label_set, model, calls, "
                       f"cost_usd, seconds) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (self.at, self.stage, self.label_set, self.model, *values))
        else:
            db.execute(self.conn, f"UPDATE {db.STORE}.spend SET calls = %s, cost_usd = %s, seconds = %s "
                       f"WHERE at = %s AND stage IS NOT DISTINCT FROM %s AND label_set IS NOT DISTINCT FROM %s "
                       f"AND model IS NOT DISTINCT FROM %s",
                       (*values, self.at, self.stage, self.label_set, self.model))
        self.conn.commit()

    def add(self, cost, calls=1, check=True):
        """What one call (or `calls` calls) paid: into the ledger at once; CapReached when the cap
        is passed (check=False records it only: a request that was in flight when the cap was reached)."""
        with self.lock:
            self.calls += calls
            self.cost += cost
            self._write()
            left = cap_left(self.conn)
        if check and left is not None and left < 0:
            raise CapReached(f"{self.stage}: --max-spend reached after {self.calls:,} calls; "
                             f"the answers so far are stored")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.calls:
            with self.lock:
                self._write()               # the step's last seconds
        return False


# ----------------------------------------------------------- the cap's window
#
# --max-spend caps a run as a whole. A run that stops before it ends (a kill, a
# failed request, the cap) and is started again with the same command keeps the
# window it opened, so what the earlier attempts spent counts against the same
# cap; the window closes when that command ends without error.

def _windows_path(data):
    from wbg_portfolio.paths import where
    return where(data, "reports", "spend_windows.json")


def _windows(data):
    try:
        with open(_windows_path(data), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def open_window(data, command, cap):
    """Set the cap for this process (and the stages it starts) and return a line saying so.
    `command` names the run (its stages and selection, not the cap): the same command
    started again before it ended keeps its window."""
    from wbg_portfolio import common
    from wbg_portfolio import db
    key = json.dumps({"store": db.STORE, **command}, sort_keys=True)
    windows = _windows(data)
    since = windows.get(key)
    if not since:
        since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        windows[key] = since
        common.write_atomic(_windows_path(data), json.dumps(windows, indent=1, sort_keys=True))
        note = ""
    else:
        note = " (the same command started then and did not end; what it spent counts)"
    os.environ["WBG_SPEND_CAP_USD"] = f"{cap:.4f}"
    os.environ["WBG_SPEND_SINCE"] = since
    return f"spend cap US${cap:.2f}, counted from {since}{note}"


def close_window(data, command):
    """The command ended without error: its window closes."""
    from wbg_portfolio import common
    from wbg_portfolio import db
    key = json.dumps({"store": db.STORE, **command}, sort_keys=True)
    windows = _windows(data)
    if windows.pop(key, None):
        common.write_atomic(_windows_path(data), json.dumps(windows, indent=1, sort_keys=True))
