"""What the evaluation fitted and the stages read: probability layers and
skip gates, one row per kind, label set and label in the store
(db.STORE.fits), each keyed on the exact question it was fitted for."""
import json

from wbg_portfolio import db
from wbg_portfolio import panel


def write(conn, kind, label_set, specs):
    """specs = {label_id: spec with question_sha}; replaces the kind's rows
    for the set (none fitted = the rows go)."""
    with conn.transaction():
        conn.execute(f"DELETE FROM {db.STORE}.fits WHERE kind = %s AND label_set = %s", (kind, label_set))
        for lid, spec in sorted(specs.items()):
            conn.execute(f"INSERT INTO {db.STORE}.fits (kind, label_set, label_id, question_sha, spec, "
                         f"fitted_at) VALUES (%s, %s, %s, %s, %s, now())",
                         (kind, label_set, lid, spec.get("question_sha"), json.dumps(spec)))


def read(conn, kind, label_set, question_shas):
    """{label_id: spec} of the fits that match each label's current question."""
    if not db.exists(conn, f"{db.STORE}.fits"):
        return {}
    got = db.rows(conn, f"SELECT label_id, question_sha, spec FROM {db.STORE}.fits WHERE kind = %s "
                  f"AND label_set = %s", (kind, label_set))
    return {lid: spec for lid, qsha, spec in got if question_shas.get(lid) == qsha}


def gates(conn, label_set, question_shas, search_run, start):
    """{label_id: gate}: the fitted gate where it matches the question and the
    search run whose cosines it read, else the panel's starting gate."""
    fitted = read(conn, "gate", label_set, question_shas)
    return {lid: (float(fitted[lid]["gate"]) if lid in fitted and
                  fitted[lid].get("search_run") == search_run else start) for lid in question_shas}


def layers(conn, kind, label_set, question_shas, vector_of):
    """{label_id: posterior.Layer} while posterior.on, else {}."""
    if not panel.get("posterior.on", False):
        return {}
    from wbg_portfolio import posterior
    return {lid: posterior.Layer(spec, vector_of)
            for lid, spec in read(conn, "layer", label_set, question_shas).items()}
