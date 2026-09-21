"""Fixture tests for the gold-set sample builder.

The point of the fixture is that the real run happens on the box against a
20 MB corpus and a worksheet that cannot be committed, so every failure mode
has to be reachable here instead: a moved clean file, a reordered tracker
column, a slice that overlaps another, a workbook whose ids do not join.
"""
import csv, hashlib, importlib.util, os, random, re, sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "build_sample", os.path.join(ROOT, "analysis", "goldset", "build_sample.py"))
bs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bs)

openpyxl = pytest.importorskip("openpyxl")

MEASURE = ("The Project will finance the burial of fiber optic cable along "
           "one hundred and twenty kilometres of flood prone segments of the "
           "national backbone corridor near the coast.")
OTHER = ("The Recipient shall prepare and furnish to the Association interim "
         "unaudited financial reports for the Project covering the calendar "
         "semester not later than forty five days after the period ends.")
THIRD = ("Component two will support the establishment of a national computer "
         "security incident response team together with a security operations "
         "centre serving all line ministries and their agencies.")


def write_corpus(tmp, paragraphs):
    """paragraphs: [(doc_id, project_id, section, block, text)] -> data dir."""
    data = tmp / "data"
    (data / "clean").mkdir(parents=True)
    prep = data / "intermediate" / "prepared"
    prep.mkdir(parents=True)

    bodies, rows, links = {}, [], {}
    for i, (doc, proj, section, block, text) in enumerate(paragraphs):
        body = bodies.setdefault(doc, "")
        start = len(body)
        bodies[doc] = body + text + "\n\n"
        rows.append({
            "paragraph_id": f"{doc}:p{i:03d}", "doc_id": doc, "ordinal": i,
            "section_label": section, "in_scope": "true", "block_type": block,
            "char_start": start, "char_end": start + len(text),
            "raw_text_sha256": "", "clean_version": "clean-1",
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "token_count": len(text.split()),
        })
        links[doc] = proj
    for doc, body in bodies.items():
        (data / "clean" / f"{doc}.txt").write_text(body)
    with open(prep / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    with open(prep / "span_project.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["doc_id", "project_id", "link_basis"])
        for doc, proj in sorted(links.items()):
            w.writerow([doc, proj, "test"])
    return data


# The column names src/clean.py actually writes (PARA_COLS at clean.py:138).
# data/paragraphs.csv carries these and no `block_type`/`section_label`/
# `token_count`/`in_scope`, and no span_project.csv beside it.
PARA_COLS_ON_DISK = ["paragraph_id", "doc_id", "project_ids", "ordinal",
                     "section_path", "section_title", "block", "char_start",
                     "char_end", "n_tokens", "text_sha256"]


def rewrite_on_disk_schema(data, drop_span_project=True):
    """Re-write the fixture corpus as the box's table, not the data model's."""
    prep = data / "intermediate" / "prepared"
    links = {r["doc_id"]: r["project_id"]
             for r in csv.DictReader(open(prep / "span_project.csv"))}
    rows = list(csv.DictReader(open(prep / "paragraphs.csv")))
    with open(prep / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARA_COLS_ON_DISK)
        w.writeheader()
        for r in rows:
            w.writerow({
                "paragraph_id": r["paragraph_id"], "doc_id": r["doc_id"],
                "project_ids": links.get(r["doc_id"], ""),
                "ordinal": r["ordinal"], "section_path": "ANNEX 2",
                "section_title": r["section_label"], "block": r["block_type"],
                "char_start": r["char_start"], "char_end": r["char_end"],
                "n_tokens": "7", "text_sha256": r["text_sha256"],
            })
    if drop_span_project:
        (prep / "span_project.csv").unlink()
    return data


def write_tracker(path, entries, extra_col=False):
    """entries: [(project, excerpt, {category: value})]."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2. Activities Review"
    cats = ["Resilient telecom", "Climate applications1"]
    hdr = (["junk", "Project ID"] + (["inserted later"] if extra_col else [])
           + ["Measures / Activities", "Adaptation"] + cats + ["Mitigation"])
    ws.append(["banner"] + [""] * (len(hdr) - 1))
    ws.append(hdr)
    for proj, text, mapping in entries:
        row = {"Project ID": proj, "Measures / Activities": text,
               "Adaptation": "Yes"}
        row.update(mapping)
        ws.append([row.get(h, "") for h in hdr])
    wb.save(path)


def args(**kw):
    base = dict(data="", out="", seed=7, n_climate=1, n_components=1,
                n_other=1, n_probe=0, per_project_cap=3, probe_cap=2,
                targeted=0, targeted_sections="", targeted_projects="",
                targeted_project_sections="component|annex", rows_rich=3,
                rows_sparse=2, inspect=False)
    base.update(kw)
    return type("A", (), base)()


def corpus_fixture(tmp):
    return write_corpus(tmp, [
        ("d1", "P100001", "Project Components", "narrative", MEASURE),
        ("d1", "P100001", "Fiduciary", "narrative", OTHER),
        ("d1", "P100001", "Cybersecurity arrangements", "annex", THIRD),
        ("d2", "P100002", "Project Components", "narrative", MEASURE + " Two."),
        ("d2", "P100002", "Annex 4 Climate", "annex", THIRD + " Again."),
        ("d3", "P100003", "Fiduciary", "narrative", OTHER + " More."),
    ])


def test_resolves_text_and_links_projects(tmp_path, capsys):
    data = corpus_fixture(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    assert len(paras) == 6
    by_id = {p["paragraph_id"]: p for p in paras}
    assert by_id["d1:p000"]["text"] == MEASURE
    assert by_id["d1:p000"]["project_id"] == "P100001"
    assert by_id["d1:p002"]["block_type"] == "annex"


def test_moved_clean_file_aborts_rather_than_yielding_wrong_text(tmp_path):
    data = corpus_fixture(tmp_path)
    p = data / "clean" / "d1.txt"
    p.write_text("x" + p.read_text())          # shift every offset by one
    with pytest.raises(SystemExit) as e:
        bs.load_corpus(str(data), lambda m: None)
    assert "text_sha256" in str(e.value)


def test_out_of_scope_and_non_prose_are_dropped(tmp_path):
    data = write_corpus(tmp_path, [
        ("d1", "P1", "Components", "narrative", MEASURE),
        ("d1", "P1", "Table", "table", OTHER),
    ])
    rows = list(csv.DictReader(open(data / "intermediate" / "prepared" / "paragraphs.csv")))
    rows[0]["in_scope"] = "false"
    with open(data / "intermediate" / "prepared" / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    assert bs.load_corpus(str(data), lambda m: None) == []


def test_tracker_columns_are_found_by_name_not_position(tmp_path):
    a = tmp_path / "a.xlsx"; b = tmp_path / "b.xlsx"
    entries = [("P100001", MEASURE, {"Resilient telecom": "Yes"})]
    write_tracker(a, entries)
    write_tracker(b, entries, extra_col=True)
    ra = bs.read_tracker(str(a), lambda m: None)
    rb = bs.read_tracker(str(b), lambda m: None)
    assert ra == rb == [("P100001", MEASURE, ["Resilient telecom"])]


def test_tracker_categories_stop_at_the_mitigation_flag(tmp_path):
    path = tmp_path / "t.xlsx"
    write_tracker(path, [("P100001", MEASURE, {"Climate applications1": "EWS"})])
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    ws.cell(2, ws.max_column + 1, "EE telecom infra")     # a mitigation column
    ws.cell(3, ws.max_column, "Yes")
    wb.save(path)
    _, _, cats = bs.read_tracker(str(path), lambda m: None)[0]
    assert cats == ["Climate applications1"]


def test_category_hint_maps_to_asset_and_direction():
    assert bs.hint_for(["Resilient telecom"]) == ("telecom network", "resilience_of_asset")
    assert bs.hint_for(["Climate applications1"])[1] == "digital_for_resilience"
    assert bs.hint_for(["Something nobody defined"]) == ("", "")


def test_on_disk_schema_is_read_when_the_modelled_columns_are_absent(tmp_path):
    """The crash was span_project.csv; the silent one was the column names.

    paragraphs.csv on the box carries block/section_title/n_tokens and no
    in_scope. Reading only the modelled names finds no prose paragraph at all
    and writes an empty workbook, which is worse than the crash.
    """
    data = rewrite_on_disk_schema(corpus_fixture(tmp_path))
    paras = bs.load_corpus(str(data), lambda m: None)
    assert len(paras) == 6
    by_id = {p["paragraph_id"]: p for p in paras}
    assert by_id["d1:p000"]["block_type"] == "narrative"
    assert by_id["d1:p000"]["section_label"] == "Project Components"
    assert by_id["d1:p000"]["token_count"] == 7
    assert by_id["d1:p002"]["block_type"] == "annex"


def test_missing_span_project_falls_back_to_the_project_ids_column(tmp_path):
    """span_project.csv is the modelled home of the link and is not on the box."""
    data = rewrite_on_disk_schema(corpus_fixture(tmp_path))
    assert not (data / "intermediate" / "prepared" / "span_project.csv").exists()
    paras = bs.load_corpus(str(data), lambda m: None)
    assert {p["project_id"] for p in paras} == {"P100001", "P100002", "P100003"}


def test_pipe_delimited_project_ids_link_to_the_first_project(tmp_path):
    data = rewrite_on_disk_schema(corpus_fixture(tmp_path))
    prep = data / "intermediate" / "prepared"
    rows = list(csv.DictReader(open(prep / "paragraphs.csv")))
    for r in rows:
        r["project_ids"] += "|P999999"
    with open(prep / "paragraphs.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PARA_COLS_ON_DISK)
        w.writeheader(); w.writerows(rows)
    paras = bs.load_corpus(str(data), lambda m: None)
    assert {p["project_id"] for p in paras} == {"P100001", "P100002", "P100003"}


def test_named_projects_fill_the_targeted_slice_when_headings_do_not(tmp_path):
    """On the real corpus four paragraphs sit under a heading naming these
    topics, so the named-project selector is what makes the slice possible."""
    data = write_corpus(tmp_path, [
        ("d1", "P100001", "Project Components", "narrative", MEASURE),
        ("d1", "P100001", "Fiduciary", "narrative", OTHER),
        ("d2", "P100002", "Project Components", "narrative", THIRD),
    ])
    paras = bs.load_corpus(str(data), lambda m: None)
    pool, why = bs.targeted_pool(
        paras, args(targeted_projects="p100001",
                    targeted_project_sections="component"), lambda m: None)
    assert [p["paragraph_id"] for p in pool] == ["d1:p000"]
    assert "1 of 1 named projects" in why


def test_named_projects_and_headings_union_without_duplicating(tmp_path):
    data = write_corpus(tmp_path, [
        ("d1", "P100001", "Project Components", "narrative", MEASURE),
        ("d2", "P100002", "Cybersecurity annex", "annex", THIRD),
    ])
    paras = bs.load_corpus(str(data), lambda m: None)
    pool, _ = bs.targeted_pool(
        paras, args(targeted_sections="cyber|component",
                    targeted_projects="P100001",
                    targeted_project_sections="component"), lambda m: None)
    ids = [p["paragraph_id"] for p in pool]
    assert ids == sorted(set(ids)) and len(ids) == 2


def test_an_unknown_project_id_is_reported_not_swallowed(tmp_path, capsys):
    data = write_corpus(tmp_path, [
        ("d1", "P100001", "Project Components", "narrative", MEASURE)])
    paras = bs.load_corpus(str(data), lambda m: None)
    bs.targeted_pool(paras, args(targeted_projects="P100001,P404040",
                                 targeted_project_sections="component"),
                     lambda m: print(m))
    assert "P404040" in capsys.readouterr().out


# --- strata, caps and the probe -------------------------------------------

CYBER = ("The project will establish a national computer security incident "
         "response team, CSIRT, and a security operations centre covering all "
         "line ministries, with incident response procedures and drills.")


def wide_corpus(tmp, n_projects=8, per_project=6):
    """Enough projects that a per-project cap can actually bind."""
    rows = []
    for i in range(n_projects):
        proj = f"P{100000 + i}"
        for j in range(per_project):
            section = ("Annex 4 Climate Co-Benefits" if j == 0 else
                       "Project Components" if j < 4 else "Fiduciary")
            body = CYBER if j == 3 else MEASURE if j < 3 else OTHER
            rows.append((f"d{i}", proj, section, "narrative",
                         f"{body} Sentence {i}-{j} keeps the text distinct."))
    return write_corpus(tmp, rows)


def test_strata_are_disjoint_and_first_match_wins():
    compiled = [(n, re.compile(rx, re.I) if rx else None)
                for n, rx in bs.STRATA]
    # a climate co-benefit ANNEX is climate, not components, though both match
    assert bs.stratum_of({"section_label": "Annex 4 Climate Co-Benefits"},
                         compiled) == "climate"
    assert bs.stratum_of({"section_label": "Project Components"},
                         compiled) == "components"
    assert bs.stratum_of({"section_label": "Fiduciary"}, compiled) == "other"
    assert bs.stratum_of({"section_label": ""}, compiled) == "other"


def test_the_per_project_cap_binds(tmp_path):
    """The failure this cap exists for: four consecutive paragraphs of one
    document reported as a four-paragraph sample."""
    data = wide_corpus(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    pool = [p for p in paras if "Components" in p["section_label"]]
    got = bs.draw(pool, 12, 2, random.Random(1), set(),
                  lambda m: None, "t")
    spread = Counter(p["project_id"] for p in got)
    assert spread.most_common(1)[0][1] <= 2
    assert len(spread) >= 6, "the draw must spread over projects, not stack"


def test_the_cap_limits_the_total_when_projects_run_out(tmp_path):
    data = wide_corpus(tmp_path, n_projects=2, per_project=6)
    paras = bs.load_corpus(str(data), lambda m: None)
    pool = [p for p in paras if "Components" in p["section_label"]]
    got = bs.draw(pool, 12, 2, random.Random(1), set(),
                  lambda m: None, "t")
    assert len(got) == 4, "2 projects x cap 2"


def test_draw_never_returns_an_already_taken_paragraph(tmp_path):
    data = wide_corpus(tmp_path)
    paras = bs.load_corpus(str(data), lambda m: None)
    rng = random.Random(3)
    taken = set()
    a = bs.draw(paras, 6, 3, rng, taken, lambda m: None, "a")
    b = bs.draw(paras, 6, 3, rng, taken, lambda m: None, "b")
    assert not ({p["paragraph_id"] for p in a} & {p["paragraph_id"] for p in b})


def test_probe_matches_bodies_and_not_certification_or_social(tmp_path):
    data = write_corpus(tmp_path, [
        ("d1", "P1", "Project Components", "narrative", CYBER),
        ("d1", "P1", "Project Components", "narrative",
         "The certification of social protection beneficiaries will proceed."),
    ])
    paras = bs.load_corpus(str(data), lambda m: None)
    hit = bs.probe_pool(paras, lambda m: None)
    assert [p["paragraph_id"] for p in hit] == ["d1:p000"], \
        "case-insensitive CERT/SOC would match certification and social"


def test_probe_is_its_own_group_and_the_strata_are_untouched(tmp_path):
    data = wide_corpus(tmp_path)
    out = tmp_path / "o.xlsx"
    bs.build(args(data=str(data), out=str(out), n_climate=4, n_components=6,
                  n_other=4, n_probe=4), lambda m: None)
    wb = openpyxl.load_workbook(out)
    slices = Counter(r[4].value for r in wb["paragraphs"].iter_rows(min_row=2))
    assert slices["probe"] == 4
    assert set(slices) == {"climate", "components", "other", "probe"}


def test_the_whole_build_is_deterministic_and_joins(tmp_path):
    data = wide_corpus(tmp_path)
    seen = []
    for i in range(2):
        out = tmp_path / f"o{i}.xlsx"
        bs.build(args(data=str(data), out=str(out), n_climate=4,
                      n_components=6, n_other=4, n_probe=3), lambda m: None)
        wb = openpyxl.load_workbook(out)
        pids = [r[0].value for r in wb["paragraphs"].iter_rows(min_row=2)]
        rows = [r[0].value for r in wb["rows"].iter_rows(min_row=2)]
        assert len(pids) == len(set(pids)), "a paragraph appears twice"
        assert set(rows) <= set(pids), "a rows entry has no paragraph"
        seen.append((pids, rows))
    assert seen[0] == seen[1]


def test_other_gets_fewer_blank_rows_than_the_rest():
    a = args(rows_rich=4, rows_sparse=2)
    assert bs.blank_rows("other", a) == 2
    for name in ("climate", "components", "probe"):
        assert bs.blank_rows(name, a) == 4
