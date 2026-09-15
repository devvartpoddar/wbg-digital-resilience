#!/usr/bin/env python3
"""Fetch World Bank appraisal documents (text renditions) for the cohort.

Reads  inputs/config/cohort.csv  (hand-maintained; included=true rows only)
Writes data/raw/{doc_id}.txt     (gitignored)
       data/documents.csv        (one row per fetched document)
       data/fetch_report.txt     (what was skipped and why)

Resumable: a document whose raw file already exists is not re-fetched.
Run again after an interruption and it picks up where it stopped.
"""
import argparse, csv, hashlib, json, os, sys, time
import urllib.parse
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WDS = "https://search.worldbank.org/api/v3/wds"

# Identify the caller rather than arriving as an anonymous script.
HEADERS = {"User-Agent": "wbg-digital-resilience/0.1 (research; "
                         "+https://github.com/devvartpoddar/wbg-digital-resilience)"}

# The appraisal document of an investment operation, and of its additional
# financings. A project routinely has one PAD and several Project Papers.
DOC_TYPES = ("Project Appraisal Document", "Project Paper")

# A genuine restructuring paper can be 15 KB, so the floor is low and the real
# guard is the HTML check below: the Bank's silent-403 body is a 118-byte page.
MIN_BYTES = 3_000

# One row per document, not per project-document pair. A regional programme
# discloses one appraisal document that serves several projects, so project_ids
# is pipe-delimited: keying on project_id instead would repeat the document and
# emit duplicate paragraph identifiers downstream.
DOC_COLS = ["doc_id", "project_ids", "doc_type", "doc_kind", "title",
            "disclosure_date", "lang", "source_url", "bytes", "content_sha256",
            "has_markers", "fetched_at"]


def looks_like_error_page(blob):
    head = blob[:600].lstrip().lower()
    return head.startswith(b"<html") or b"<title>403" in head or b"404 not found" in head


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
        "fl": "id,docty,display_title,disclosure_date,txturl,pdfurl,lang"})
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
            "txturl": val.get("txturl") or "",
        })
    return [d for d in out if d["doc_id"]]


def read_cohort(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["included"].strip().lower() == "true"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=os.path.join(ROOT, "inputs/config/cohort.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    ap.add_argument("--limit", type=int, default=0, help="first N projects only (for a smoke run)")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = ap.parse_args()

    raw_dir = os.path.join(args.out, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    projects = read_cohort(args.cohort)
    if args.limit:
        projects = projects[:args.limit]
    print(f"cohort: {len(projects)} projects included", flush=True)

    by_doc, notes = {}, []
    counts = {"fetched": 0, "cached": 0, "too_small": 0, "no_txturl": 0,
              "no_docs": 0, "error": 0}

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

        if not docs:
            counts["no_docs"] += 1
            notes.append(f"{pid}\tno appraisal documents returned")
            continue

        for doc in docs:
            dest = os.path.join(raw_dir, f"{doc['doc_id']}.txt")

            if not doc["txturl"]:
                counts["no_txturl"] += 1
                notes.append(f"{pid}\t{doc['doc_id']}\tno txturl published")
                continue

            if os.path.exists(dest) and os.path.getsize(dest) >= MIN_BYTES:
                blob = open(dest, "rb").read()
                # Keep the original acquisition time; this run did not fetch it.
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(dest)))
                counts["cached"] += 1
            else:
                try:
                    blob = get(doc["txturl"]).content
                except Exception as exc:
                    counts["error"] += 1
                    notes.append(f"{pid}\t{doc['doc_id']}\tFETCH FAILED\t{exc}")
                    continue
                # Guard against the silent-403 case before anything is stored.
                if looks_like_error_page(blob) or len(blob) < MIN_BYTES:
                    counts["too_small"] += 1
                    why = "HTML error page" if looks_like_error_page(blob) else f"only {len(blob)} bytes"
                    notes.append(f"{pid}\t{doc['doc_id']}\t{why}, not stored")
                    continue
                tmp = dest + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(blob)
                os.replace(tmp, dest)
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                counts["fetched"] += 1
                time.sleep(args.delay)

            text = blob.decode("utf-8", errors="replace")
            if doc["doc_id"] in by_doc:
                by_doc[doc["doc_id"]]["_projects"].add(pid)
                continue
            by_doc[doc["doc_id"]] = {
                "_projects": {pid},
                "doc_id": doc["doc_id"], "doc_type": doc["doc_type"],
                "doc_kind": classify(doc["doc_type"], doc["title"]),
                "title": doc["title"], "disclosure_date": doc["disclosure_date"],
                "lang": doc["lang"], "source_url": doc["txturl"], "bytes": len(blob),
                "content_sha256": hashlib.sha256(blob).hexdigest(),
                "has_markers": "true" if "@#&OPS" in text else "false",
                "fetched_at": stamp,
            }

        if n % 10 == 0:
            print(f"  ...{n}/{len(projects)} projects, {len(by_doc)} documents", flush=True)

    rows = []
    for did in sorted(by_doc):
        row = dict(by_doc[did])
        row["project_ids"] = "|".join(sorted(row.pop("_projects")))
        rows.append(row)
    # Deterministic order so a re-run produces the same file.
    rows.sort(key=lambda r: r["doc_id"])
    docs_csv = os.path.join(args.out, "documents.csv")
    with open(docs_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DOC_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    seen = {p for r in rows for p in r["project_ids"].split("|")}
    missing = [p["project_id"] for p in projects if p["project_id"] not in seen]
    report = os.path.join(args.out, "fetch_report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"projects in cohort (included): {len(projects)}\n")
        fh.write(f"projects with >=1 document:    {len(seen)}\n")
        fh.write(f"documents written:             {len(rows)}\n")
        for k, v in counts.items():
            fh.write(f"  {k}: {v}\n")
        no_markers = [r['doc_id'] for r in rows if r['has_markers'] == 'false']
        kinds = {}
        for r in rows:
            kinds[r["doc_kind"]] = kinds.get(r["doc_kind"], 0) + 1
        fh.write("by kind:\n")
        for k in sorted(kinds):
            fh.write(f"  {k}: {kinds[k]}\n")
        fh.write(f"documents without @#&OPS markers: {len(no_markers)}\n")
        if no_markers:
            fh.write("  " + ", ".join(no_markers[:40]) + "\n")
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
