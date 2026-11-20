# Running the pipeline

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 0. Running it

```
./wbg_portfolio run                                  # fetch > prepare > embed > search > classify > measure > link > activities > publish
./wbg_portfolio run --from search --measures cra     # a changed measure, from its search to Postgres
./wbg_portfolio run --only measure --measures a b c  # three measures through the funnel, together
./wbg_portfolio run --from classify                  # the asset definitions changed: the map and everything after it
./wbg_portfolio run --to prepare --project P180495   # one project's documents and tables
./wbg_portfolio run --from classify --max-spend 5    # stop before any request that would pass US$5
./wbg_portfolio run --dry-run --from classify        # estimates only; nothing asked, nothing written
./wbg_portfolio add cra                              # the same as run --from search --to measure --measures cra
./wbg_portfolio publish                              # every decision read again from the scores, then the views
```

| Stage | What it does | Reads | Writes |
|---|---|---|---|
| fetch | the appraisal documents, procurement records and project records the selected projects lack | the Bank's interfaces | `data/raw/` |
| prepare | read the PDFs, clean, sentences, components, procurement tables (the plan PDFs in `data/raw/plan_pdf/` fill blank components and amounts); load the corpus tables and cut a release when they changed | `data/raw/` | `data/appraisal/`, `data/procurement/`, Postgres corpus tables |
| embed | the sentence vectors, only what the cache lacks | `data/appraisal/` | `data/embeddings/` |
| search | every label set's phrases against the vectors (cosines) | `data/embeddings/`, the label files | `data/search/<set>-<version>/` |
| classify | the asset map, first pass: the funnel over the four assets (section 5b) | the search run, the store | `wbg.scores` (kind asset) |
| measure | every measure through the funnel, then its second look in the band (decision model with the paragraph, chat model grade); then once for all committed sentences the digital question, the policy flag, and for the digital ones the assets' second look and kinds; then the types (they need none of those); the pair checks only while `band.on` is false (5b, 5c, 5d, 5g) | the search runs, the store, `wbg.scores` | `wbg.scores` (kinds measure, measure_ctx, measure_second, digital, policy, asset_second, asset_subtype, type, pair) |
| link | which asset each package has (first pass, then the chat model's second look on the band), and each package's fit to the measures of its project, the strong link (5e); the old link question only while `link.strengths` is false | `data/procurement/packages.csv`, `wbg.scores` | `wbg.scores` (kinds package_asset, package_asset_second, package_measure, link) |
| activities | the distinct activities of each project x measure group of committed, digital sentences, by the chat model (section 5f) | `wbg.scores` | `wbg.scores` (kind activity) |
| publish | every decision table from the scores as the control panel reads them now, then the views (5g) | `wbg.scores`, the panel, the fits | schema `wbg` |
| audit | the parse and procurement audits, only with `--to audit` or `--only audit` | | `data/reports/audit*.txt` |

The model stages run in one process, with one Postgres connection and one reading of the corpus. Each asks only what the store lacks and stops on a failed request; the next run resumes. **Spend.** `--max-spend X` caps what the whole run may spend: the ledger (`wbg_store.spend`) is read before each step and the step stops before a request that would pass what is left; a stop or a refusal still lands in the ledger. `--dry-run` skips fetch and prepare and prints each model stage's estimate (stored answers, requests to make, about how much) without writing anything.

**The measure stage's second looks (2026-11-20).** They honour `--project` (only those projects' sentences are asked and only their rows replaced), `--dry-run` (an estimate line per step: `second:<set>:context`, `second:<set>:chat`, `digital`, `policy`, `asset_second`, `asset_subtype`) and `--max-spend`, and ask nothing the store holds; a pair whose second look failed or could not be read waits (not committed) and the run says how many. What they cost (measured 2026-11-20): the decision model's context asks about US$0.03 per 1,000 pairs, the chat model's second looks about US$0.08 per 1,000 with reasoning off (`second.reasoning` false; a reply is about 10 tokens). For the full data about 25,000 pairs are expected in the band, so about US$0.75 for the context asks and US$2.00 for the chat grades the first time, plus the digital question (decision model) and the policy flag (chat model) for each committed sentence. `./wbg_portfolio status` shows the rows of each new kind.

**The link and activities stages (2026-11-20).** `link` and `activities` honour `--project`, `--dry-run` and `--max-spend` the same way and print an estimate line per step (`link:assets`, `package_asset_second`, `package_measure:<set>`, `activities`). A dry run estimates what the stored scores before it make askable: on an empty store the later steps show zero, because nothing is committed yet. The activities step costs about US$0.06 per 1,000 sentences (reasoning on, `activities.reasoning` true, at most `activities.max_tokens` 4000 a reply). A dry run's estimate is an upper bound: it prices every reply at its token cap (`second.max_tokens` 1000, `activities.max_tokens` 4000), so it reads well above what a run costs. A reply the chat model gives that is not the JSON asked for is stored as a failure, counted and asked again at the next run; it is never read as a yes.

**Isolation.** `WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store ./wbg_portfolio run ...` writes scores, decision tables and views into `wbg_v2` and reuses every stored answer (the store is additive and keyed on model, exact state and exact question); `WBG_DATA` points at a copy of `data/`. `$WBG_STORE` defaults to `<schema>_store`.

**What a run cost.** The run prints one line per stage when it ends and appends it to `data/reports/run_stages.jsonl`, one line per run; a run that stops on a failed stage records what ran before it. `src/wbg_portfolio/run_report.py` reads the last line, the spend ledger for that run's window (folded to the pipeline stages) and the evaluation's precision and recall per measure, and writes the note a person reads (AGENTS.md rule 8). The note carries numbers only; the reader adds the decisions.

**No option overrides a threshold.** Every gate, floor, depth and round setting is a line of `inputs/config/control_panel.csv` (section 5g); the commands take a selection, `--dry-run`, `--max-spend` and the worker count, nothing else that moves a number.

**Selecting projects.** Filters on the included rows of `inputs/config/cohort.csv`: `--project`; `--country` (code); `--region` (part of the name); `--fy` (approval fiscal year); `--practice` (Global Practice name or code, or part); `--unit` (managing unit or code, or part); `--set` (`digital` or `digital_contributor`). Each takes several values; different filters combine with "and". A project outside the cohort is refused. A run over named projects writes scores for those units only and is not published (set `WBG_PUBLISH_SCOPED=1` for a scratch database). `./wbg_portfolio list` prints a selection, `./wbg_portfolio summary` where each project stands.

## Running beside the live data

To try this code on the real corpus without touching `wbg` (the schema the dashboards read) or `data/`, run it into another schema, reading the stored answers of the live store. Nothing in `wbg`, `wbg_store`'s existing answers or `data/` changes: a test (`tests/test_pipeline_e2e.py`) publishes into one schema over another schema's store and checks that nothing in the first is created, dropped or written.

1. A copy of the data folder, because the corpus stages re-read components and rewrite files under it:

   ```
   cp -a /ygg/projects/wbg-digital-resilience/data /ygg/projects/wbg-digital-resilience/data-v2
   ```

2. The environment, in every shell that runs the pipeline for this trial:

   ```
   export WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store WBG_DATA=/ygg/projects/wbg-digital-resilience/data-v2
   export WBG_PANEL=inputs/config/control_panel_v2.csv
   ```

   `inputs/config/control_panel.csv` keeps the live behaviour (every new switch off, every old setting at its old value), so a publish from it changes nothing the dashboards show. `control_panel_v2.csv` is the same file with the new behaviour on (`band.on`, `digital.on`, `link.strengths`, `policy.on`, `activities.on`, `floor.types` 0.6, `asset.multiple`, no pair checks, `second_look` links); a test keeps the two in step. A relative `WBG_PANEL` is read from the repository root. `WBG_SCHEMA` is where scores, decision tables and views go; `WBG_STORE` is where answers are read from and added to (the record is additive: it only gains answers); `WBG_DATA` is the copy. `./wbg_portfolio status` now describes `wbg_v2`.
3. Bring the copy up to date with the code, which asks no model for answers (new search phrases are embedded; that is a few cents): `./wbg_portfolio run --to search`.

   **Before any paid run, read what the new component rules changed.** A lettered component read in a document that has only numbered ones moves every later sentence to another component, and the component is part of every sentence's question, so those sentences are asked again. After the re-parse, list the documents whose `component_number` values differ from the live `wbg.paragraphs`; zero documents is the expected answer for those that have only numbered components. It reads the copy's `appraisal/paragraphs.csv` and the live table, writes nothing and asks no model (run it with the box's Python, from the repository root):

   ```
   PYTHONPATH=src WBG_DATA=/ygg/projects/wbg-digital-resilience/data-v2 python - <<'PY'
   import csv, collections
   from wbg_portfolio import db
   from wbg_portfolio.paths import where, data_root
   conn = db.connect()
   live = {p: c or "" for p, c in conn.execute("SELECT paragraph_id, component_number FROM wbg.paragraphs")}
   changed = collections.defaultdict(list)
   with open(where(data_root(), "appraisal", "paragraphs.csv"), newline="", encoding="utf-8") as fh:
       for r in csv.DictReader(fh):
           if r["paragraph_id"] in live and live[r["paragraph_id"]] != (r["component_number"] or ""):
               changed[r["doc_id"]].append((live[r["paragraph_id"]], r["component_number"]))
   for doc, ch in sorted(changed.items(), key=lambda x: -len(x[1])):
       print(doc, len(ch), "paragraph(s); first:", ch[0][0] or "-", "->", ch[0][1] or "-")
   print(len(changed), "document(s) changed")
   PY
   ```

   Read the first rows against the document before going on. (`sentence_index.csv` and `components.csv` in the two data folders hold the same numbers if a file diff is easier.)
4. A single-project dry run, to see what each step would ask: `./wbg_portfolio run --from classify --dry-run --project <project id>`. It prints one estimate line per step. On a fresh `wbg_v2` the steps after the first show little, because they wait on the scores before them.
5. The same project for real, with a small cap: `./wbg_portfolio run --from classify --project <project id> --max-spend 0.50`. A run over named projects is not published (`WBG_PUBLISH_SCOPED=1` publishes it into `wbg_v2` for a look: it is a scratch schema, so that is safe here). Read the output, then `./wbg_portfolio status`. Also read the count of unreadable chat replies in the log; more than a few per hundred means the reasoning switch is not being honoured - stop and report.
6. The full dry run, to read the estimate: `./wbg_portfolio run --from classify --dry-run`. Stop and ask if it is more than expected (the second looks of about 25,000 pairs and the digital question for each committed sentence are the bulk, section above).
7. The full run: `./wbg_portfolio run --from classify --max-spend <the estimate, rounded up>`. If the cap stops it, run it again: it resumes from the store.

**Comparing `wbg_v2` with `wbg`** (psql, database `work`; the first three are model readings and will differ, which is the point):

```sql
-- 1. committed sentences per measure
SELECT 'wbg' AS schema, label_set, count(*) FROM wbg.measure_findings WHERE is_committed GROUP BY label_set
UNION ALL
SELECT 'wbg_v2', label_set, count(*) FROM wbg_v2.measure_findings WHERE is_committed AND is_digital GROUP BY label_set
ORDER BY label_set, schema;
-- 2. commitments (project x asset x measure x type) and projects with one
SELECT 'wbg' AS schema, count(*) AS commitments, count(DISTINCT project_id) AS projects FROM wbg.commitments
UNION ALL SELECT 'wbg_v2', count(*), count(DISTINCT project_id) FROM wbg_v2.commitments;
-- 3. packages linked: the old link question against the strengths
SELECT 'wbg old link' AS source, count(DISTINCT package_id) FROM wbg.commitment_packages WHERE posterior_probability >= 0.7
UNION ALL SELECT 'wbg_v2 strong', count(DISTINCT package_id) FROM wbg_v2.commitment_package_links WHERE strength = 'strong'
UNION ALL SELECT 'wbg_v2 asset', count(DISTINCT package_id) FROM wbg_v2.commitment_package_links;
-- 4. what the new tables add
SELECT (SELECT count(DISTINCT activity_id) FROM wbg_v2.activities) AS activities,
       (SELECT count(*) FROM wbg_v2.not_digital_findings) AS committed_not_digital,
       (SELECT count(*) FROM wbg_v2.unit_assets WHERE asset_level = 'second_look') AS asset_rows_from_second_look;
```

The ids of the two schemas' assets differ (four assets against thirteen), so compare assets by count, not by id. Delete the trial with `DROP SCHEMA wbg_v2 CASCADE` (never `wbg_store`) and remove `data-v2` only after asking.

## What a change costs

Answers are the only thing that costs money. An answer is stored under the model, the exact state sent (the sentence with its lead-in, section and component; a package with its component; a commitment and a package) and the exact question (its wording and scale, not its id). A stage asks only the keys the store lacks. So:

- **Nothing**: a set whose questions, gates, patterns, search run and round settings match its last finished run is skipped whole (`wbg.funnel_state` holds the fingerprint): no store read, no scores rewritten. The pair-check and link stages always cover every active measure, whatever subset a run names, because each replaces its whole kind.
- **A decision floor, `asset.*`, `types.multiple`, `floor.pair_check`, `floor.types`, `band.keep`, `band.context`, `band.second_grade`, `digital.floor`, `posterior.*`, `dashboard.*`**: nothing. `./wbg_portfolio publish` rereads the scores (a minute or two). `band.low` changes which pairs get a second look: only the pairs newly in the band are asked. `second.model` or `second.context_chars` asks every second look again.
- **A definition, a phrase or a pattern edit**: only the sentences whose question changed, or that the new gate or pattern newly admits. `./wbg_portfolio run --from search --measures <set>`.
- **An ask floor (`floor.ask`, `asset.ask_floor`), a gate, a round setting, `link.*`**: the sentences or pairs newly let in. The feedback rounds run only when round 0 asked something new of a set already scored, so a re-run after a refit does not move the admitted pool.
- **`ask.context`**: every sentence-level question is a new state. A whole-cohort re-ask (about US$6 to 9 at the current corpus and gates). Test it first on the graded sentences.
- **A measure of another family** (added, edited or switched off): nothing. A measure's own questions hold only its own definition, and the pair checks, the only questions that put two definitions side by side, pair measures of one family only. What every family shares, and so re-asks every family when changed: the grade scale, the asset map, `ask.context`, `floor.ask` and the model.
- **A new document**: its own sentences only.
- **The code of a corpus stage** (projects, clean, components, clean_procurement): that stage and what follows it run again, at no model cost; `clean` reads every PDF again (hours). Moving the code into the `wbg_portfolio` package (2026-10-25) changed every file's bytes, so the first `./wbg_portfolio run` after it rebuilds the corpus once; the parse check (`text_sha256`) then shows whether the text came out the same.
- **The model snapshot (`questions.MODEL`)**: everything; a deliberate step followed by re-measurement.

Estimates (2026-10-22, 251 projects, about 150,000 searched sentences, starting gates 0.3): embedding about US$1; the asset map US$0.8 to 1.2; twelve measures US$3 to 5; types, pair checks and package links US$1 to 2; **about US$6 to 9 for a clean run**. The dry run gives the figure for the corpus as it stands.

## Clean run on the box

1. The key: `.secrets/wbg-openrouter-key` in the main checkout, mode 0600, owned by the account that runs the pipeline; or `$WBG_OPENROUTER_KEY`. Postgres: `postgresql:///work` (`$WBG_PG` overrides).
2. `./wbg_portfolio check import`: the grades of the file-based sheets (`data/labels/*_units.csv`, `pairs.csv`, `package_assets.csv`, `package_links.csv`) into `wbg_store.grades`, once.
3. `./wbg_portfolio run --to search`: fetch, prepare, embed, search. The first run after the parser-version change of 2026-10-24 reads every PDF again (hours); embeddings come from the cache where the text is unchanged.
4. `./wbg_portfolio run --from classify --max-spend <US$>`: the asset map and the measures from the store (every answer asked before is reused: the old file store is read once and copied in), the types and pair checks re-asked (their state now carries the section and component), the links from the store.
5. `./wbg_portfolio check evaluate`: every kind against every grade; the layers and gates fitted and written; `data/reports/evaluate.md` and `wbg.measure_quality`. Set the panel from it; `./wbg_portfolio publish`.
6. Grading rounds: `./wbg_portfolio check draw --seed <n> --design train` writes `data/review/tasks_s<n>.jsonl`; a person or an agent writes `fills_s<n>.jsonl` (`{"task_id", "grade", "rationale"}` per task); `./wbg_portfolio check fill --seed <n> --file fills_s<n>.jsonl --grader <who>`; `./wbg_portfolio check evaluate`; `./wbg_portfolio publish`. `./wbg_portfolio check spotcheck --seed <n> --file fills_s<n>.jsonl` writes a thin workbook for a person to check an agent's fills. An evaluation draw (`--design eval`) is graded afresh and fits nothing; its figures are the ones to quote.

## Adding a measure

A measure is three small files and one command; the only part a person must write is the definition:

- `analysis/labels/<id>.csv`: `measure_id`, `measure_name`, `definition` (three to six sentences: what counts, what does not; name the measure, never the digital asset, the asset map supplies that link), `boundary` (optional, read by the pair checks), `theme`, `family` (the family it belongs to, one per measure: `climate`, `cloud_data`; see section 5c), `active`, `version`;
- `<id>_queries.csv`: six to ten search phrases, each a sentence a document might write;
- `<id>_patterns.csv` (optional): one or two whole-word patterns (`\bunderground\b`, not `ducts?`, which also matches "conducted");
- `<id>_types.csv` (optional): the approved types; without one, the first run proposes a list into `data/types/<id>_types.csv` for a person to approve into the labels folder.

The grade scale is shared (`analysis/labels/scale.csv`); a `<id>_scale.csv` beside the set overrides it. Then `./wbg_portfolio add <id>` (several: `./wbg_portfolio add a b c`). Stop and ask if the stage's estimate is above US$1. Merging two types (`merge_into`) or renaming one (`display_name`) is a file edit and a publish; dropping a type or changing its definition re-asks that measure's types.

## Exploring: a scratch measure

`./wbg_portfolio try <slug>` answers "what is there on X?" before X is a measure. It writes the three label files under `data/scratch/` (outside git), then runs the measures' own search and funnel: the phrases are embedded, the gate and the word patterns choose the sentences, the decision model grades each on the shared scale, the feedback rounds look below the gate, and every answer is stored and reused. The scores land in `wbg.scores` with kind `scratch`, which the publish never reads, so the commitments do not move; the command prints what it found (`scratch.summary`).

```
./wbg_portfolio try e_waste --expand "e-waste: collection, recycling or safe disposal of ICT equipment" --max-spend 0.50
./wbg_portfolio try e_waste --definition "..." --phrase "..." --phrase "..." --pattern '\be-waste\b'
./wbg_portfolio try e_waste --project P171305           # one project only
./wbg_portfolio try --list                              # what has been tried, with counts
```

`--expand` is one generative call (`scratch.GEN_MODEL`, a few cents, stored under its ask so the same ask gives the same files) that writes a name, a definition of 60 to 100 words, 8 to 10 phrases and up to 3 patterns; they are printed, and `--definition`, `--phrase` or `--pattern` given alongside override them. A sentence counts at `floor.default`. Trying again with a sharper definition re-asks only the sentences whose question changed. A scratch set graduates when a person copies its files into `analysis/labels/` (a pull request) and runs `./wbg_portfolio add <slug>`; the stored answers are reused.

`./wbg_portfolio status` prints the brief an agent or a person needs at the start of a session (`src/wbg_portfolio/status.py`): the release lines, the active measures, scores per kind, stored answers, grades, fits, spend, the last publish, the scratch measures tried, whether a key is present.

## What the app sees

Schema `wbg` only, through the functions of `src/wbg_portfolio/app.py` (section 5f); the record schema `wbg_store` is for the pipeline and for querying grades and spend. Every count in the findings is the pipeline's decision, not a person's: an evaluation draw's weighted precision and recall (`wbg.measure_quality` with `sheet` = evaluation) is the number to quote beside it.
