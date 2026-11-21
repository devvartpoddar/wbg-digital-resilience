"""Helpers every stage needs: CSV tables, checksums of text and files, a small
content-addressed JSON store written aside and renamed, and what a long step needs to run
unattended: a file written whole or not at all (write_atomic), a steady "n of total" line
(Progress) and a time limit on each heavy item (limited).
"""
import csv
import hashlib
import json
import os
import threading
import time

from wbg_portfolio.paths import file_sha256  # noqa: F401  (re-exported: one checksum for every stage)


def sha_text(s):
    """The SHA-256 hex digest of a string (as UTF-8) or of bytes; None is ''."""
    if not isinstance(s, (bytes, bytearray)):
        s = (s or "").encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def read_csv(path):
    """The rows of a CSV table as dicts; an absent file is an empty table."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _part(path):
    """A temporary name beside path, unique to this process and thread, so two writers of one
    file never share it."""
    return f"{path}.{os.getpid()}.{threading.get_ident()}.part"


def write_atomic(path, data):
    """Write bytes or text to path whole or not at all: written aside, then renamed into
    place, so a kill part way through leaves the old file (or none), never half a file that
    breaks the next start."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = _part(path)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_csv(path, cols, rows):
    """Write rows under cols, atomically; columns a row lacks are blank,
    columns it has beyond cols are left out."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = _part(path)
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def json_path(root, sha):
    return os.path.join(root, sha[:2], sha + ".json")


def read_json(root, sha):
    """The stored object under this key, or None."""
    try:
        with open(json_path(root, sha), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_json(root, sha, obj):
    write_atomic(json_path(root, sha), json.dumps(obj, ensure_ascii=False, sort_keys=True))


# ------------------------------------------------------------------ long steps

def say(line):
    """print, flushed at once: a step's output reaches a log file or a service's journal as it
    is written, not when a buffer fills."""
    print(line, flush=True)


# The exit status of a stage that finished but left items out (a PDF that could not be read
# in time): ./wbg_portfolio run goes on with what it has, and does not record the stage as up
# to date, so the next run tries those items again (cli.prepare).
PARTIAL = 3
SKIPPED = []          # (what, item, why) of every item a Progress in this process skipped


def exit_status(code=0):
    """A stage script's exit status: `code` when it is not 0, else PARTIAL when an item was
    skipped in this process, else 0. `sys.exit(common.exit_status(main()))`."""
    return code or (PARTIAL if SKIPPED else 0)


# How often a long step says where it is, at most and at least while items finish: often
# enough that a watcher can tell a step that works from one that is stuck.
PROGRESS_SECONDS = 60


class Progress:
    """The steady lines of a long step: "<what>: n of total, <note> (s)" on the first item,
    then whenever PROGRESS_SECONDS have passed since the last line, and on the last item; a
    line for every item skipped, and at the end how many were skipped and which.

        p = Progress("clean", len(docs), log)
        for doc in docs:
            ...                      # p.skip(doc_id, why) for an item left out
            p.step(f"{doc_id} read")
        p.end()
    """

    def __init__(self, what, total, log=say, clock=time.monotonic):
        self.what, self.total, self.log, self.clock = what, total, log, clock
        self.n, self.skipped, self.t0, self.last = 0, [], clock(), None

    def step(self, note=""):
        """One more item done; a line when one is due."""
        self.n += 1
        now = self.clock()
        if self.last is None or now - self.last >= PROGRESS_SECONDS or self.n >= self.total:
            self.last = now
            self.log(f"{self.what}: {self.n:,} of {self.total:,}" + (f", {note}" if note else "")
                     + f" ({now - self.t0:.0f} s)")

    def skip(self, item, why):
        """One more item done, left out: always a line, naming it and why."""
        self.skipped.append((item, why))
        SKIPPED.append((self.what, item, why))
        self.n += 1
        self.last = self.clock()
        self.log(f"{self.what}: {self.n:,} of {self.total:,}: {item} skipped: {why}")

    def end(self):
        """The closing line when items were skipped; returns [(item, why)]."""
        if self.skipped:
            names = ", ".join(str(i) for i, _ in self.skipped[:20])
            more = f" and {len(self.skipped) - 20:,} more" if len(self.skipped) > 20 else ""
            self.log(f"{self.what}: {len(self.skipped):,} of {self.total:,} skipped "
                     f"(tried again on the next run): {names}{more}")
        return self.skipped


# A PDF reads in about a second a page (docs/method/procurement.md); a read is stopped at three
# times that, and never sooner than a minute, so only a read that would not finish is cut.
PDF_SECONDS_PER_PAGE = 3
PDF_MIN_SECONDS = 60


def pdf_seconds(path):
    """The time limit for reading one PDF, from its page count (a PDF whose pages cannot be
    counted gets the least)."""
    try:
        import pypdfium2
        doc = pypdfium2.PdfDocument(path)
        try:
            pages = len(doc)
        finally:
            doc.close()
    except Exception:                               # noqa: BLE001 - the read reports it
        pages = 0
    return max(PDF_MIN_SECONDS, PDF_SECONDS_PER_PAGE * pages)


# The most memory one item's reader may take. An item that needs more is a fault in how it is
# read, and must fail on its own (it is named and left out) rather than push the machine into
# swap and stall everything else running on it.
CHILD_MEMORY = 6 * 1024 ** 3


def _limited_child(fn, item, conn):
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY, CHILD_MEMORY))
        conn.send(("ok", fn(item)))
    except BaseException as exc:                    # noqa: BLE001 - reported to the parent
        conn.send(("error", f"{type(exc).__name__}: {str(exc)[:200]}"))
    finally:
        conn.close()


def _stop(proc, conn):
    if proc.is_alive():
        proc.terminate()
    proc.join(5)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
    conn.close()


def limited(fn, items, seconds, workers=1):
    """fn(item) for each item, each in a child process of its own that is stopped after
    `seconds` (a number, or a function of the item): one item that never finishes, or that
    brings its reader down (memory, a crash in a library), is reported and the others go on.

    Yields (item, result, error) as each finishes, up to `workers` at once; error is '' or
    why the item has no result. Stopping the generator (the caller stopped) stops the
    children still running."""
    import multiprocessing as mp
    from multiprocessing.connection import wait
    ctx = mp.get_context("fork")
    todo, running = list(items), {}
    try:
        while todo or running:
            while todo and len(running) < max(1, workers):
                item = todo.pop(0)
                limit = seconds(item) if callable(seconds) else seconds
                parent, child = ctx.Pipe(duplex=False)
                proc = ctx.Process(target=_limited_child, args=(fn, item, child), daemon=True)
                proc.start()
                child.close()
                running[parent] = (item, proc, time.monotonic() + limit, limit)
            due = min(d for _, _, d, _ in running.values())
            ready = wait(list(running), max(0.0, due - time.monotonic()))
            for conn in ready:
                item, proc, _, _ = running.pop(conn)
                try:
                    kind, value = conn.recv()
                except (EOFError, OSError):
                    kind, value = "error", f"the reader stopped without an answer (exit {proc.exitcode})"
                _stop(proc, conn)
                yield (item, value, "") if kind == "ok" else (item, None, value)
            now = time.monotonic()
            for conn, (item, proc, deadline, limit) in list(running.items()):
                if now >= deadline:
                    del running[conn]
                    _stop(proc, conn)
                    yield item, None, f"not finished within {limit:.0f} seconds"
    finally:
        for conn, (_item, proc, _d, _l) in running.items():
            _stop(proc, conn)
