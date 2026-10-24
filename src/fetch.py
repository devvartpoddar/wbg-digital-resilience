#!/usr/bin/env python3
"""Fetch the cohort's appraisal documents (PDFs).

Reads  inputs/config/cohort.csv (included=true rows)
Writes data/raw/pdf/{doc_id}.pdf, data/raw/documents.csv (one row per document),
       data/reports/fetch.txt, data/reports/fetch_status/appraisal.json

Resumable: a PDF already on disk is not fetched again.
"""
import argparse, csv, os, re, sys, tempfile, time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402
from common import read_csv, sha_text, write_csv  # noqa: E402
from paths import data_root, where, write_fetch_status  # noqa: E402
WDS = "https://search.worldbank.org/api/v3/wds"

# Identify the caller rather than arriving as an anonymous script.
HEADERS = {"User-Agent": "wbg-digital-resilience/0.1 (research; "
                         "+https://github.com/devvartpoddar/wbg-digital-resilience)"}

# The appraisal document of an investment operation, and of its additional financings.
DOC_TYPES = ("Project Appraisal Document", "Project Paper")

# One row per document, not per project-document pair.
DOC_COLS = ["doc_id", "project_ids", "doc_type", "doc_kind", "title",
            "disclosure_date", "lang", "fetched_at", "pdf_url", "pdf_bytes", "pdf_sha256",
            "owner_unit"]


def classify(doc_type, title):
    """docty='Project Paper' conflates two different things: the appraisal of an
    additional financing, and a restructuring paper that merely records changes.
    Only the first is an appraisal document. Keep both, label them, decide later."""
    if doc_type == "Project Appraisal Document":
        return "pad"
    low = (title or "").lower()
    if "additional financ" in low:
        return "additional_financing"
    if "restructur" in low:
        return "restructuring"
    return "project_paper_other"


# Status codes asked again (with connection errors).
RETRY = frozenset({403, 408, 429} | set(range(500, 600)))
REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5
# A PDF is whole when it opens with %PDF and its %%EOF marker sits in this many final bytes
# (an incremental update or trailing newline can follow it).
PDF_TAIL = 2048


def get(url, *, timeout=60, tries=6):
    """GET through api.request, following redirects here so an http://
    Location is upgraded to https: the proxy refuses plain http and answers
    with a 403 page that would be stored as the document."""
    for _ in range(MAX_REDIRECTS):
        r = api.request("GET", url, timeout=timeout, tries=tries, retry=RETRY,
                        headers=HEADERS, allow_redirects=False)
        if r.status_code not in REDIRECTS:
            return r
        url = urllib.parse.urljoin(url, r.headers.get("location", ""))
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
    raise api.Transport(f"too many redirects from {url}")


def now_stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def complete_pdf(blob):
    """Whether a body is a whole PDF: the %PDF header and %%EOF near the end."""
    return blob.startswith(b"%PDF") and b"%%EOF" in blob[-PDF_TAIL:]


def write_atomic(dest, blob):
    """Write aside under a name unique to this call, then rename into place,
    so two threads fetching one file never share a temporary file."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest),
                               prefix=os.path.basename(dest) + ".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def list_docs(project_id, doc_type):
    """Every document of one type for one project. rows is generous on purpose:
    capping this silently truncates busy projects and hides their PAD."""
    q = urllib.parse.urlencode({
        "format": "json", "rows": "50", "projectid": project_id, "docty": doc_type,
        "fl": "id,docty,display_title,disclosure_date,pdfurl,lang,owner"})
    data = get(f"{WDS}?{q}", timeout=45).json()
    out = []
    for key, val in data.get("documents", {}).items():
        if key == "facets":
            continue
        out.append({
            "doc_id": str(val.get("id") or "").strip(),
            "doc_type": doc_type,
            "title": (val.get("display_title") or "").replace("\xa0", " ").strip(),
            "disclosure_date": (val.get("disclosure_date") or "")[:10],
            "lang": val.get("lang") or "",
            "pdfurl": val.get("pdfurl") or "",
            "owner_unit": " ".join((val.get("owner") or "").split()),
        })
    return [d for d in out if d["doc_id"]]


def fetch_pdf(doc, pdf_dir, counts, notes, pid, delay):
    """(bytes, fetched_at) of the PDF behind a document, cached on disk;
    fetched_at is the time of this download, blank for a cached file.
    (None, "") when it cannot be had: clean.py reads the PDF only."""
    dest = os.path.join(pdf_dir, f"{doc['doc_id']}.pdf")
    if os.path.exists(dest):
        counts["pdf_cached"] += 1
        with open(dest, "rb") as fh:
            return fh.read(), ""
    if not doc.get("pdfurl"):
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tno pdfurl published")
        return None, ""
    try:
        stamp = now_stamp()
        blob = get(doc["pdfurl"], timeout=120).content
    except Exception as exc:
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tPDF FETCH FAILED\t{exc}")
        return None, ""
    if not complete_pdf(blob):
        # A throttled interface answers with an HTML page and status 200, and a cut
        # connection leaves a truncated file: a failed download, tried again on the next
        # run, not a fact about the document.
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tPDF FETCH FAILED\t"
                     f"pdfurl did not return a whole PDF ({len(blob)} bytes)")
        return None, ""
    write_atomic(dest, blob)
    counts["pdf_fetched"] += 1
    time.sleep(delay)
    return blob, stamp


def read_cohort(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["included"].strip().lower() == "true"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--out", default=data_root())
    ap.add_argument("--limit", type=int, default=0, help="first N projects only (for a smoke run)")
    ap.add_argument("--projects", default="",
                    help="comma-separated project ids: fetch only these; the rest keep "
                         "what earlier runs recorded")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--workers", type=int, default=4, help="projects fetched at once")
    args = ap.parse_args()

    projects = read_cohort(args.cohort)
    if args.projects:
        wanted = {p.strip() for p in args.projects.split(",") if p.strip()}
        projects = [p for p in projects if p["project_id"] in wanted]
    if args.limit:
        projects = projects[:args.limit]
    print(f"cohort: {len(projects)} projects included", flush=True)

    by_doc, notes, listed = {}, [], set()
    counts = {"no_docs": 0, "error": 0, "pdf_fetched": 0, "pdf_cached": 0,
              "pdf_missing": 0}
    pdf_dir = where(args.out, "pdf")
    os.makedirs(pdf_dir, exist_ok=True)

    def one(proj):
        """List and download one project's documents. Returns (pid, listed,
        [(doc, blob or None)], counts, notes): its own tallies, merged on
        the main thread, so four projects can run at once."""
        pid = proj["project_id"]
        c = {"no_docs": 0, "error": 0, "pdf_fetched": 0, "pdf_cached": 0, "pdf_missing": 0}
        nts, got = [], []
        try:
            docs = []
            for dt in DOC_TYPES:
                docs += list_docs(pid, dt)
                time.sleep(args.delay)
        except Exception as exc:
            c["error"] += 1
            nts.append(f"{pid}\tLISTING FAILED\t{exc}")
            return pid, False, got, c, nts
        if not docs:
            c["no_docs"] += 1
            nts.append(f"{pid}\tno appraisal documents returned")
        for doc in docs:
            got.append((doc, *fetch_pdf(doc, pdf_dir, c, nts, pid, args.delay)))
        return pid, True, got, c, nts

    docs_csv = where(args.out, "documents")
    previous = {r["doc_id"]: r for r in read_csv(docs_csv)}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = pool.map(one, projects)
        for n, (pid, ok, got, c, nts) in enumerate(results, 1):
            for k, v in c.items():
                counts[k] += v
            notes.extend(nts)
            if not ok:
                print(f"[{n}/{len(projects)}] {pid} listing failed", flush=True)
                continue
            listed.add(pid)
            for doc, pdf_blob, stamp in got:
                if doc["doc_id"] in by_doc:
                    by_doc[doc["doc_id"]]["_projects"].add(pid)
                    continue
                if not pdf_blob:
                    continue
                sha = sha_text(pdf_blob)
                # An unchanged PDF keeps the time it was first fetched, so a re-listing
                # leaves documents.csv as it was.
                old = previous.get(doc["doc_id"], {})
                if old.get("pdf_sha256") == sha and old.get("fetched_at"):
                    stamp = old["fetched_at"]
                by_doc[doc["doc_id"]] = {
                    "_projects": {pid},
                    "doc_id": doc["doc_id"], "doc_type": doc["doc_type"],
                    "doc_kind": classify(doc["doc_type"], doc["title"]),
                    "title": doc["title"], "disclosure_date": doc["disclosure_date"],
                    "lang": doc["lang"],
                    "fetched_at": stamp or now_stamp(),
                    # The interface gives some addresses as http; the same address serves
                    # https, which a browser will open.
                    "pdf_url": re.sub(r"^http://", "https://", doc["pdfurl"]),
                    "pdf_bytes": len(pdf_blob),
                    "pdf_sha256": sha,
                    "owner_unit": doc["owner_unit"],
                }
            if n % 10 == 0:
                print(f"  ...{n}/{len(projects)} projects, {len(by_doc)} documents", flush=True)

    # A project this run did not list - outside a --limit smoke run, or its listing failed -
    # keeps the documents an earlier run recorded for it, so a partial run never shrinks the
    # corpus.
    for old in previous.values():
        kept = {p for p in old["project_ids"].split("|") if p and p not in listed}
        if not kept and old["doc_id"] not in by_doc and \
                os.path.exists(os.path.join(pdf_dir, f"{old['doc_id']}.pdf")):
            # Listed, but the listing omits a document whose PDF is on disk: a shorter
            # (paged or throttled) listing must not shrink the corpus.
            kept = {p for p in old["project_ids"].split("|") if p}
            counts["kept_not_relisted"] = counts.get("kept_not_relisted", 0) + 1
        if not kept:
            continue
        if old["doc_id"] in by_doc:
            by_doc[old["doc_id"]]["_projects"] |= kept
        else:
            row = {c: old.get(c, "") for c in DOC_COLS if c != "project_ids"}
            by_doc[old["doc_id"]] = dict(row, _projects=kept)
            counts["kept_from_earlier_run"] = counts.get("kept_from_earlier_run", 0) + 1
    rows = []
    for did in sorted(by_doc):
        row = dict(by_doc[did])
        row["project_ids"] = "|".join(sorted(row.pop("_projects")))
        rows.append(row)
    rows.sort(key=lambda r: r["doc_id"])
    write_csv(docs_csv, DOC_COLS, rows)
    # A project whose listing or a PDF download failed is not counted as fetched: the next
    # run tries it again.
    asked = {p["project_id"] for p in projects}
    failed = {n.split("\t")[0] for n in notes if "FAILED" in n} & asked
    write_fetch_status(args.out, "appraisal", sorted(asked - failed), sorted(failed))

    seen = {p for r in rows for p in r["project_ids"].split("|")}
    missing = [p["project_id"] for p in projects if p["project_id"] not in seen]
    report = where(args.out, "reports", "fetch.txt")
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"projects in cohort (included): {len(projects)}\n")
        fh.write(f"projects with >=1 document:    {len(seen)}\n")
        fh.write(f"documents written:             {len(rows)}\n")
        for k, v in counts.items():
            fh.write(f"  {k}: {v}\n")
        kinds = {}
        for r in rows:
            kinds[r["doc_kind"]] = kinds.get(r["doc_kind"], 0) + 1
        fh.write("by kind:\n")
        for k in sorted(kinds):
            fh.write(f"  {k}: {kinds[k]}\n")
        fh.write(f"\nprojects yielding nothing ({len(missing)}):\n")
        for p in missing:
            fh.write(f"  {p}\n")
        fh.write("\nnotes:\n")
        for line in notes:
            fh.write(f"  {line}\n")

    print(f"\n{len(rows)} documents from {len(seen)}/{len(projects)} projects")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"wrote {docs_csv}")
    print(f"wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
