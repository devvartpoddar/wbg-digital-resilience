"""The session brief: what is true in the store right now, for a person or an
agent starting work. Printed by ./wbg_portfolio status after the release lines."""
import os

from wbg_portfolio import db
from wbg_portfolio import labels
from wbg_portfolio import questions as Q
from wbg_portfolio.paths import main_checkout


def _n(conn, sql, params=()):
    try:
        return db.one(conn, sql, params)
    except Exception:                                 # noqa: BLE001  a table not yet made
        conn.rollback()
        return None


def brief(data=None):
    lines = []
    try:
        conn = db.connect()
    except Exception as exc:                          # noqa: BLE001
        return [f"postgres: not reachable ({str(exc)[:80]})"]
    with conn:
        S, T = db.SCHEMA, db.STORE
        sets = [n for n in labels.label_sets() if n != Q.ASSETS]
        off = [n for n in labels.label_sets(include_inactive=True) if n not in sets and n != Q.ASSETS]
        lines.append(f"measures: {len(sets)} active ({', '.join(sets)})" + (f"; off: {', '.join(off)}" if off else ""))
        for kind in ("asset", "measure", "type", "pair", "package_asset", "link", "scratch"):
            r = _n(conn, f"SELECT count(*), count(DISTINCT label_set) FROM {S}.scores WHERE kind = %s", (kind,))
            if r and r[0]:
                lines.append(f"scores:   {kind:<14} {r[0]:>9,} rows, {r[1]} set(s)")
        r = _n(conn, f"SELECT count(*), count(DISTINCT model) FROM {T}.answers")
        if r:
            lines.append(f"answers:  {r[0]:,} stored, {r[1]} model(s)")
        rows = _n(conn, f"SELECT string_agg(kind || ' ' || n, ', ' ORDER BY kind) FROM "
                        f"(SELECT kind, count(*) n FROM {T}.grades GROUP BY kind) g")
        lines.append(f"grades:   {rows[0] if rows and rows[0] else 'none'}")
        r = _n(conn, f"SELECT count(*), max(fitted_at)::date FROM {T}.fits")
        if r:
            lines.append(f"fits:     {r[0]} layer(s) or gate(s)" + (f", latest {r[1]}" if r[1] else ""))
        r = _n(conn, f"SELECT coalesce(sum(cost_usd), 0), coalesce(sum(cost_usd) FILTER (WHERE at > now() - interval '7 days'), 0), "
                     f"count(*) FROM {T}.spend")
        if r:
            lines.append(f"spend:    US${float(r[0]):.2f} in all, US${float(r[1]):.2f} this week, {r[2]} step(s)")
        r = _n(conn, f"SELECT max(loaded_at) FROM {S}.loads WHERE table_name = 'measure_findings'")
        lines.append(f"publish:  {r[0].strftime('%Y-%m-%d %H:%M') if r and r[0] else 'never'}")
        r = _n(conn, f"SELECT count(*) FROM {S}.commitments")
        if r and r[0] is not None:
            lines.append(f"dashboards: {r[0]:,} commitments")
        rows = _n(conn, f"SELECT string_agg(label_set, ', ' ORDER BY written_at DESC) FROM {S}.scratch_sets")
        if rows and rows[0]:
            lines.append(f"scratch:  {rows[0]} (./wbg_portfolio try --list)")
    try:                                              # api.key() finds the key in the main checkout
        from wbg_portfolio import api
        key = "present" if api.key() else (
            f"not found: set ${api.KEY_VAR} or {os.path.join(main_checkout(), api.KEY_FILE)}")
    except SystemExit as exc:                         # world-readable: say so, do not guess
        key = str(exc)
    lines.append(f"key:      {key}")
    lines.append("read:     docs/methodology.md, then the docs/method/ file for what you change; "
                 ".agents/skills/wbg/SKILL.md says how to explore")
    return lines
