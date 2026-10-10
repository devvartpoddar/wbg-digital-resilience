"""Links between the procurement tables: which plan package a notice or a
contract award belongs to.

A notice and an award each carry the borrower reference the package was
procured under. That reference, normalised the same way (clean_procurement
.norm_ref), is the link. It is only unique within a project, so the key is
(project_id, normalised reference). A generic reference that one plan uses for
several packages ('CS-INDV' for every individual consultant) points at more
than one package; the description's match key then decides, and when it cannot
the record is left unlinked and says so. Nothing is linked on a guess.

Every linked record gets two columns:

  package_id    the package it belongs to, or blank
  package_link  how: reference, reference_and_description, ambiguous (the
                reference names several packages and the description does not
                pick one), not_in_plan (no package has this reference), or
                no_reference (the record carries none)
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
        if counters is not None:
            counters[f"{name}_link_{r['package_link']}"] += 1
    return records
