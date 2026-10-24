"""Link notices and awards to the plan package they belong to.

The key is (project_id, normalised borrower reference; clean_procurement.norm_ref).
When a reference names several packages the description's match key decides, and
otherwise the record stays unlinked. Each notice and award gets package_id and
package_link (reference, reference_and_description, ambiguous, not_in_plan,
no_reference); each award also gets the notices issued under its reference.
"""
from collections import defaultdict

LINKS = ("reference", "reference_and_description", "ambiguous", "not_in_plan",
         "no_reference")


def package_index(packages):
    """(project_id, normalised reference) -> [(package_id, description_match)]."""
    index = defaultdict(list)
    for p in packages:
        if p.get("borrower_ref_norm"):
            index[(p["project_id"], p["borrower_ref_norm"])].append(
                (p["package_id"], p.get("description_match") or ""))
    return index


def link_one(record, index):
    """(package_id, package_link) for one notice or award."""
    ref = record.get("borrower_ref_norm") or ""
    if not ref:
        return "", "no_reference"
    cands = index.get((record["project_id"], ref), [])
    if not cands:
        return "", "not_in_plan"
    if len(cands) == 1:
        return cands[0][0], "reference"
    same = [pid for pid, match in cands
            if match and match == (record.get("description_match") or "")]
    if len(same) == 1:
        return same[0], "reference_and_description"
    return "", "ambiguous"


def link_all(records, packages, counters=None, name="records"):
    """Write package_id and package_link onto every record, in place."""
    index = package_index(packages)
    for r in records:
        r["package_id"], r["package_link"] = link_one(r, index)
        assert r["package_link"] in LINKS, r["package_link"]
        if counters is not None:
            counters[f"{name}_link_{r['package_link']}"] += 1
    return records


def link_awards_to_notices(awards, notices, counters=None):
    """Write notice_ids onto every award, in place: the notices of the same
    project and normalised reference, joined with '|'. Where both the award and
    a notice are linked to a package, they must be linked to the same one, so a
    generic reference shared by several packages does not join a contract to
    another package's tender."""
    by_ref = defaultdict(list)
    for n in notices:
        if n.get("borrower_ref_norm"):
            by_ref[(n["project_id"], n["borrower_ref_norm"])].append(n)
    for a in awards:
        found = []
        if a.get("borrower_ref_norm"):
            for n in by_ref.get((a["project_id"], a["borrower_ref_norm"]), []):
                if a.get("package_id") and n.get("package_id") and \
                        n["package_id"] != a["package_id"]:
                    continue
                if not a.get("package_id") and n.get("package_link") == "ambiguous":
                    continue
                found.append(n["notice_id"])
        a["notice_ids"] = "|".join(sorted(set(found)))
        if counters is not None:
            counters["awards_with_a_notice" if found else "awards_with_no_notice"] += 1
    return awards

