#!/usr/bin/env python3
"""Fetch World Bank appraisal documents (PDFs) for the cohort.

Reads  inputs/config/cohort.csv  (hand-maintained; included=true rows only)
Writes data/raw/pdf/{doc_id}.pdf   the PDF, which src/pdf_layout.py reads
       data/raw/documents.csv      (one row per fetched document)
       data/reports/fetch.txt      (what was skipped and why)

Resumable: a document whose PDF already exists is not re-fetched.
Run again after an interruption and it picks up where it stopped.
"""
import argparse, csv, hashlib, json, os, sys, time
import urllib.parse
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import data_root, where, write_fetch_status  # noqa: E402
WDS = "https://search.worldbank.org/api/v3/wds"

# Identify the caller rather than arriving as an anonymous script.
HEADERS = {"User-Agent": "wbg-digital-resilience/0.1 (research; "
                         "+https://github.com/devvartpoddar/wbg-digital-resilience)"}

# The appraisal document of an investment operation, and of its additional
# financings. A project routinely has one PAD and several Project Papers.
DOC_TYPES = ("Project Appraisal Document", "Project Paper")

# One row per document, not per project-document pair. A regional programme
# discloses one appraisal document that serves several projects, so project_ids
# is pipe-delimited: keying on project_id instead would repeat the document and
# emit duplicate paragraph identifiers downstream.
# owner_unit is the unit the documents interface records as the document's
# owner, as printed ("Digital Dev - AFR EAST/SOUTH (IDD04)"); often blank.
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


def get(url, *, timeout=60, tries=6):
    """GET, following redirects manually so an http:// Location can be upgraded.

    About a quarter of document URLs 302 to documents1.worldbank.org over plain
    http. Left alone the proxy refuses it and the body stored is a 118-byte 403
    page that parses fine and contains nothing. Upgrading the scheme recovers
    the document intact.
    """
    for attempt in range(tries):
        try:
            seen = url
            for _ in range(5):
                r = requests.get(seen, timeout=timeout, allow_redirects=False,
                                 headers=HEADERS)
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location", "")
                    seen = urllib.parse.urljoin(seen, loc)
                    if seen.startswith("http://"):
                        seen = "https://" + seen[len("http://"):]
                    continue
                r.raise_for_status()
                return r
            raise RuntimeError("too many redirects")
        except Exception as exc:
            # A 403 here is the content delivery network throttling, not a
            # permission problem: the same URL succeeds moments later. Back off
            # further for those rather than giving up on a real document.
            if attempt == tries - 1:
                raise
            transient = "403" in str(exc) or "429" in str(exc)
            time.sleep((3 if transient else 1) * (2 ** attempt))


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
    """The PDF behind a document, cached on disk. Returns its bytes, or None.

    A document whose PDF cannot be had is noted and left out: clean.py reads
    the PDF only."""
    dest = os.path.join(pdf_dir, f"{doc['doc_id']}.pdf")
    if os.path.exists(dest):
        counts["pdf_cached"] += 1
        with open(dest, "rb") as fh:
            return fh.read()
    if not doc.get("pdfurl"):
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tno pdfurl published")
        return None
    try:
        blob = get(doc["pdfurl"], timeout=120).content
    except Exception as exc:
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tPDF FETCH FAILED\t{exc}")
        return None
    if not blob.startswith(b"%PDF"):
        counts["pdf_missing"] += 1
        notes.append(f"{pid}\t{doc['doc_id']}\tpdfurl did not return a PDF")
        return None
    tmp = dest + ".part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, dest)
    counts["pdf_fetched"] += 1
    time.sleep(delay)
    return blob


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

    for n, proj in enumerate(projects, 1):
        pid = proj["project_id"]
        try:
            docs = []
            for dt in DOC_TYPES:
                docs += list_docs(pid, dt)
                time.sleep(args.delay)
        except Exception as exc:
            counts["error"] += 1
            notes.append(f"{pid}\tLISTING FAILED\t{exc}")
            print(f"[{n}/{len(projects)}] {pid} listing failed: {exc}", flush=True)
            continue

        listed.add(pid)
        if not docs:
            counts["no_docs"] += 1
            notes.append(f"{pid}\tno appraisal documents returned")
            continue

        for doc in docs:
            if doc["doc_id"] in by_doc:
                by_doc[doc["doc_id"]]["_projects"].add(pid)
                continue
            pdf_path = os.path.join(pdf_dir, f"{doc['doc_id']}.pdf")
            cached = os.path.exists(pdf_path)
            pdf_blob = fetch_pdf(doc, pdf_dir, counts, notes, pid, args.delay)
            if not pdf_blob:
                continue
            when = os.path.getmtime(pdf_path) if cached else time.time()
            by_doc[doc["doc_id"]] = {
                "_projects": {pid},
                "doc_id": doc["doc_id"], "doc_type": doc["doc_type"],
                "doc_kind": classify(doc["doc_type"], doc["title"]),
                "title": doc["title"], "disclosure_date": doc["disclosure_date"],
                "lang": doc["lang"],
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when)),
                "pdf_url": doc["pdfurl"],
                "pdf_bytes": len(pdf_blob),
                "pdf_sha256": hashlib.sha256(pdf_blob).hexdigest(),
                "owner_unit": doc["owner_unit"],
            }

        if n % 10 == 0:
            print(f"  ...{n}/{len(projects)} projects, {len(by_doc)} documents", flush=True)

    docs_csv = where(args.out, "documents")
    # A project this run did not list - outside a --limit smoke run, or its
    # listing failed - keeps the documents an earlier run recorded for it, so a
    # partial run never shrinks the corpus.
    if os.path.exists(docs_csv):
        with open(docs_csv, newline="", encoding="utf-8") as fh:
            for old in csv.DictReader(fh):
                kept = {p for p in old["project_ids"].split("|") if p and p not in listed}
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
    # Deterministic order so a re-run produces the same file.
    rows.sort(key=lambda r: r["doc_id"])
    # Written aside and renamed, so an interrupted write never leaves a
    # truncated document list for the next partial run to merge from.
    with open(docs_csv + ".part", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DOC_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(docs_csv + ".part", docs_csv)
    # A project whose listing or a PDF download failed is not counted as
    # fetched: the next run tries it again. (A document that publishes no PDF
    # is a fact about the document, not a failure, and is not retried.)
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
