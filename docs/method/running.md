# Running the pipeline

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 0. Running it

```
./wbg_portfolio run                                  # fetch > prepare > embed > search > classify > measure > link > publish
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
| measure | every measure through the funnel; then the digital question for every sentence in the asked band of any measure; then each measure's second look in the band (decision model with the paragraph, chat model grade) for the sentences with a digital P(yes) of `digital.ask_low` or more; then the policy flag, and for the kept ones the assets' second look and then the type of every asset that counts; then the measure types (they need none of those) (5b, 5c, 5d, 5g) | the search runs, the store, `wbg.scores` | `wbg.scores` (kinds measure, measure_ctx, measure_second, digital, policy, asset_second, asset_subtype, type) |
| link | which asset each package has (first pass, then the chat model's second look on the band), the type of each asset it has, and each package's fit to its likeliest measures of its project, the strong link (5e) | `data/procurement/packages.csv`, `wbg.scores` | `wbg.scores` (kinds package_asset, package_asset_second, package_asset_subtype, package_measure) |
| publish | every decision table from the scores as the control panel reads them now, the link levels and the commitments (the commitment rule, no model call), then the views (5g) | `wbg.scores`, the panel, the fits | schema `wbg` |
| audit | the parse and procurement audits, only with `--to audit` or `--only audit` | | `data/reports/audit*.txt` |

The model stages run in one process, with one Postgres connection and one reading of the corpus. Each asks only what the store lacks and stops on a failed request; the next run resumes. **Spend.** A run that includes a stage that can call a paid model (`embed`, `search`, `classify`, `measure`, `link`; and `try`) does not start without `--max-spend X` (or `--dry-run`); the message says so. `--max-spend X` caps what the whole run may spend: the ledger (`wbg_store.spend`) is read before each step and the step stops before a request that would pass what is left, and again after each request. Each step's ledger row is written at its first request and brought up to date at every request, so a kill loses at most the request in flight from the ledger. The cap counts from the moment the command first started: a run that stops before it ends (a kill, a failed request, the cap) and is started again with the same command (same stages, measures and projects; the cap may differ) counts what the earlier starts spent, and the run prints `spend cap US$X, counted from <time>` with a note when it is such a restart. The window is recorded in `data/reports/spend_windows.json` and closes when the command ends without error. It counts everything in the ledger since that time, other commands' spend included, so a window left open by a run that was never finished can only stop a run early, never let it spend more. The embedding of sentences, of new search phrases and of new package descriptions (stage `link:embed`) is counted and capped like the questions. When the cap is reached, the requests already in flight finish, are stored, and their cost still reaches the ledger. A chat step's estimate, which the cap checks before the step starts, is the ledger's measured cost per request of that step (or, when the step has no record yet, of the chat model over every step), once there are 20 paid requests to average; only with neither is it the upper bound (every reply priced at `second.max_tokens`). The step's estimate line says which it used (`estimate measured: ...` or `estimate upper bound`). `--dry-run` skips fetch and prepare and prints each model stage's estimate (stored answers, requests to make, about how much) without writing anything.

**The measure stage's second looks.** They honour `--project` (only those projects' sentences are asked and only their rows replaced), `--dry-run` (an estimate line per step: `digital`, `second:<set>:context`, `second:<set>:chat`, `policy`, `asset_second`, `asset_subtype`; each says how many answers are stored and how many requests it would make, which is what a changed question re-asks) and `--max-spend`, and ask nothing the store holds; a pair whose second look failed or could not be read waits (not committed) and the run says how many. What they cost (measured 2026-11-20): the decision model's context asks about US$0.03 per 1,000 pairs, the chat model's second looks about US$0.08 per 1,000 with reasoning off (`second.reasoning` false; a reply is about 10 tokens). For the full data about 25,000 pairs are expected in the band `[band.low, band.keep)`; the wider asked band (`band.ask_low` 0.5 to `band.ask_high` 1.01, see decisions.md, "What is asked and what decides") adds about 5,000 more (about 7,000 kept pairs above `band.keep`, less those already in the band), a few thousand package x asset pairs (`link.asset_ask_high` 1.01) and about 3,000 committed, digital sentences for the chat model's asset opinion (`asset.ask_high` 1.01), all at the same per-1,000 prices (about US$0.11 per 1,000 measure pairs for both looks; US$0.08 per 1,000 for the chat looks). The dry run's step lines name the asked range. The digital question is asked first and the second looks only for sentences with a digital P(yes) of `digital.ask_low` (0.20) or more, so the counts below are upper bounds: the share that is not digital is not saved until the first run has shown it. So about US$0.75 for the context asks and US$2.00 for the chat grades the first time, plus the digital question (decision model) for every sentence with a first score in the asked band, about 25,000 sentences at about US$0.035 per 1,000 decision-model context questions, under US$1, and the policy flag (chat model) for each committed, digital sentence. `./wbg_portfolio status` shows the rows of each new kind.

**The link stage.** `link` honours `--project`, `--dry-run` and `--max-spend` the same way and prints an estimate line per step (`link:assets`, `package_asset_second`, `package_asset_subtype`, `package_measure:<set>`). A dry run estimates what the stored scores before it make askable: on an empty store the later steps show zero, because nothing is kept yet. A chat step's estimate is its measured cost per request where the ledger has one, else an upper bound that prices every reply at its token cap (`second.max_tokens` 1000) and reads well above what a run costs; the line says which. The commitments are made at publish from the kept sentences and cost nothing. A reply the chat model gives that is not the JSON asked for is stored as a failure, counted and asked again at the next run; it is never read as a yes.

**Isolation.** `WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store ./wbg_portfolio run ...` writes scores, decision tables and views into `wbg_v2` and reuses every stored answer (the store is additive and keyed on model, exact state and exact question); `WBG_DATA` points at a copy of `data/`. `$WBG_STORE` defaults to `<schema>_store`.

**What a run cost.** The run prints one line per stage when it ends and appends it to `data/reports/run_stages.jsonl`, one line per run; a run that stops on a failed stage records what ran before it. `src/wbg_portfolio/run_report.py` reads the last line, the spend ledger for that run's window (folded to the pipeline stages) and the evaluation's precision and recall per measure, and writes the note a person reads (AGENTS.md rule 8). The note carries numbers only; the reader adds the decisions.

**No option overrides a threshold.** Every gate, floor, depth and round setting is a line of `inputs/config/control_panel.csv` (section 5g); the commands take a selection, `--dry-run`, `--max-spend` and the worker count, nothing else that moves a number.

**Selecting projects.** Filters on the included rows of `inputs/config/cohort.csv`: `--project`; `--country` (code); `--region` (part of the name); `--fy` (approval fiscal year); `--practice` (Global Practice name or code, or part); `--unit` (managing unit or code, or part); `--set` (`digital` or `digital_contributor`). Each takes several values; different filters combine with "and". A project outside the cohort is refused. A run over named projects writes scores for those units only and is not published (set `WBG_PUBLISH_SCOPED=1` for a scratch database). `./wbg_portfolio list` prints a selection, `./wbg_portfolio summary` where each project stands.

**The cohort file is the scope.** `inputs/config/cohort.csv` is the scope for fetching and for every paid question: a sentence or a procurement package is asked about only when at least one of its projects is an `included = true` row (`questions.Corpus.in_scope`, `checks.load_packages`; a document shared by an included and a not included project stays in scope). A project marked not included is not asked about even when its documents are already parsed; its paragraphs and search units stay in the published corpus tables, which are text and not decisions, but it gets no question and no score row. Each paid stage prints how many sentences are in scope and how many are left out, and `./wbg_portfolio status` shows the count of included projects.

## Running a long stage unattended

A full run takes hours and is often run by an agent whose process can be stopped at any moment. It is built to be stopped and started again with the same command.

- **Run it as a service, with its output in a log file**, so it outlives the shell that started it, for example `systemd-run --user --unit wbg-run --working-directory=<checkout> ./wbg_portfolio run --from classify --max-spend 25` and `journalctl --user -u wbg-run -f`, or `nohup ./wbg_portfolio run ... > data/reports/run.log 2>&1 &` and `tail -f` on the log. Output is not buffered: the command and every stage it starts (`PYTHONUNBUFFERED`) write each line as it is printed.
- **Give the unit a memory ceiling** (`systemd-run --user -p MemoryMax=8G -p MemorySwapMax=0 ...`), so a fault in a stage can never push the machine into swap and stall the other services on it. Each PDF reader has its own ceiling of 6 GB as well (`common.CHILD_MEMORY`): a reader that needs more fails alone and its PDF is named and left out. Plan PDFs are read a page at a time and each page is released; held in memory a plan costs about 13 MB a page, over 20 GB for the longest.
- **Watch the lines.** Every long step prints `<step>: n of total` on its first item, then at least once a minute while items finish, and on its last (`common.Progress`): the fetches (plan PDFs, notices, awards; plan listings one line a project), the PDF reads of `clean`, the component tables, the plan PDF tables, the embedding requests and every model step (`<stage>: requests: n of total, k failed, US$...`). A step whose line has not moved for several minutes is stuck; one that prints is working. An item a step leaves out is named on its own line, with why, and counted at the end of the step (`... skipped (tried again on the next run): ...`).
- **Each heavy item has a time limit.** A PDF (appraisal document, component table pages, plan) is read in a child process that is stopped after three seconds a page, never less than a minute (`common.limited`, `common.pdf_seconds`; a PDF reads in about a second a page): a PDF that never finishes, or brings its reader down, is named and left out, and the others go on. A stage that left items out ends with status 3: the run goes on with what it has and runs that stage again the next time (it then reads only those items). Every network request has a timeout and a bounded number of retries (`api.request`).
- **What a restart keeps.** Everything finished: every PDF read (`data/appraisal/cache/`, `components_cache.json`, `plan_pdf_rows.csv`, each written as soon as its item is read), every downloaded file, every vector (one file each), every answer (committed as it arrives) and its cost in the ledger, the funnel's feedback rounds (`wbg.funnel_rounds`, so a restart replays the same rounds from the store), and every file written whole or not at all (`common.write_atomic`: a kill mid-write leaves the old file). The stages before the one that stopped skip in seconds: a fetch only asks for projects not yet fetched (a project whose fetch failed is tried again on the next day's run, or at once with `--update`, and is named each time), and each prepare stage runs only when its code or inputs changed (or when it left items out). Nothing paid for is asked again; the cap counts the earlier starts (above). A test kills `run --from classify --to publish` before each of its requests in turn and in the middle of the publish, starts it again, and checks that no request is repeated and that the published schema is the same as an uninterrupted run's (`tests/test_pipeline_e2e.py`).
- **What it does not keep.** A stage's work on the item in flight (one PDF, one request). A fetch stopped part way lists every project of that fetch again on the next start (the files already downloaded are not downloaded again). The publish is one transaction: stopped, it leaves the last publish in place and is done again whole.

## Running beside the live data

To try this code on the real corpus without touching `wbg` (the schema the dashboards read) or `data/`, run it into another schema, reading the stored answers of the live store. Nothing in `wbg`, `wbg_store`'s existing answers or `data/` changes: a test (`tests/test_pipeline_e2e.py`) publishes into one schema over another schema's store and checks that nothing in the first is created, dropped or written. This branch is the new pipeline; the live system keeps running from `main`, and merging this branch to `main` is the switch, so it is merged only when the dashboards are moved to the new schema.

1. A copy of the data folder, because the corpus stages re-read components and rewrite files under it:

   ```
   cp -a /ygg/projects/wbg-digital-resilience/data /ygg/projects/wbg-digital-resilience/data-v2
   ```

2. The environment, in every shell that runs the pipeline for this trial:

   ```
   export WBG_SCHEMA=wbg_v2 WBG_STORE=wbg_store WBG_DATA=/ygg/projects/wbg-digital-resilience/data-v2
   ```

   `WBG_SCHEMA` is where scores, decision tables and views go; `WBG_STORE` is where answers are read from and added to (the record is additive: it only gains answers); `WBG_DATA` is the copy. `./wbg_portfolio status` then describes `wbg_v2`. The settings are the one file, `inputs/config/control_panel.csv`.
   With `types.gaps` on, the run also proposes types for sentences no listed type fits: for each measure the committed, digital sentences no approved type reaches go to a generative model once, which proposes "OTH." types. They are published in `measure_types` marked as proposed (their name starts "OTH."), and written to `data/types/<measure>_oth_types.csv` (the copy's `data-v2`). A person reads them and moves the good ones into `analysis/labels/<measure>_types.csv` by pull request. This costs one generative call per measure, a few cents, counted by `--max-spend`; an answer already stored is not asked again.
3. Bring the copy up to date with the code, which asks no model for answers (new search phrases are embedded; that is a few cents): `./wbg_portfolio run --to search --max-spend 2`.

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

**Check the store first.** Every paid stage starts by printing the answer store it uses and how many answers it holds (per model), for example `store wbg_store (scores in wbg_v2): 412,000 answers (...)`. Setting `WBG_SCHEMA` without `WBG_STORE` makes the store `<schema>_store`, which is empty: every question would be asked and paid for again. A run with an empty store, under any pair other than the default `wbg` and `wbg_store`, therefore refuses to start (plain message, non-zero exit). Say `--fresh-store` only when a new, empty store is really meant.

**Comparing `wbg_v2` with `wbg`** (psql, database `work`; the first three are model readings and will differ, which is the point):

```sql
-- 1. committed sentences per measure
SELECT 'wbg' AS schema, label_set, count(*) FROM wbg.measure_findings WHERE is_committed GROUP BY label_set
UNION ALL
SELECT 'wbg_v2', label_set, count(*) FROM wbg_v2.measure_findings WHERE is_committed GROUP BY label_set
ORDER BY label_set, schema;
-- 2. commitments (wbg: project x asset x measure x type; wbg_v2: the commitment rule) and projects with one
SELECT 'wbg' AS schema, count(*) AS commitments, count(DISTINCT project_id) AS projects FROM wbg.commitments
UNION ALL SELECT 'wbg_v2', count(*), count(DISTINCT project_id) FROM wbg_v2.commitment_summary;
-- 3. packages linked, strong and by any link
SELECT 'wbg_v2 strong' AS source, count(DISTINCT package_id) FROM wbg_v2.commitment_package_links WHERE strength = 'strong'
UNION ALL SELECT 'wbg_v2 any link', count(DISTINCT package_id) FROM wbg_v2.commitment_package_links;
-- 4. the digital rule, the commitments by key and the chat model's asset second look
SELECT (SELECT count(*) FROM wbg_v2.commitments WHERE commitment_key = 'paragraph') AS paragraph_buckets,
       (SELECT count(*) FROM wbg_v2.not_digital_findings) AS rule_but_not_digital,
       (SELECT count(*) FROM wbg_v2.unit_assets WHERE asset_level = 'second_look') AS asset_rows_from_second_look;
```

The ids of the two schemas' assets differ (four assets against thirteen), so compare assets by count, not by id. Delete the trial with `DROP SCHEMA wbg_v2 CASCADE` (never `wbg_store`) and remove `data-v2` only after asking.

## What a change costs

Answers are the only thing that costs money. An answer is stored under the model, the exact state sent (the sentence with its lead-in, section and component; a package with its description and component) and the exact question (its wording and scale, not its id). A stage asks only the keys the store lacks, so changing anything that sits inside a state or a question asks again for every answer that held it. Settle the things below, in this order, before paying; the first rows cost the most to redo.

| What must be final before paying | What it re-asks | About |
|---|---|---|
| The parse: the document set, `data/appraisal/*.csv`, `data/text/*.txt` (section titles, component names and numbers, lead-ins, sentence and paragraph boundaries) and `data/procurement/packages.csv` (description, component, category) | Every sentence under anything that changed, in every stage; a changed sentence is also embedded again. Check a dry run against the store on exactly the data to be paid on. | US$10 to 12 |
| The grade scale (`analysis/labels/scale.csv`, or a `<set>_scale.csv`) and the decision model version (`questions.MODEL`) | Every first pass (the assets and every measure) and every second look of the decision model | US$10 to 12 |
| The chat model version (`second.model`) | Every chat second look, the policy flag, the asset second look and types, the package second look and the package asset types | US$4 |
| The taxonomy scale (`inputs/taxonomy/assets_scale.csv`) or the digital flag definition (`inputs/taxonomy/flags.csv`, row `digital`) | The scale: every asset first pass, every package asset question and the digital question. The digital definition: the digital question for every sentence in the asked band, then the later questions of the sentences it newly lets in | US$1 to 2 |
| An asset definition (`inputs/taxonomy/assets.csv`) or the shared rule (`questions.ASSET_RULE`) | That asset's (or every asset's) first pass, its package questions (first pass and second look), and the asset second look of every kept sentence; its search runs again (a few cents of embedding) | US$1 each |
| A measure definition or name (`analysis/labels/<id>.csv`) | That measure's first pass (and its feedback rounds), its second looks and its package-to-measure question; its search runs again | US$0.50 each |
| The paragraph note (`questions.CONTEXT_NOTE`) | Every question that carries the paragraph and uses it: the measure context look of every pair in the asked band, the digital question, the asset second look and the asset types (the policy question keeps its own note) | US$2 to 3 |
| A measure type (name or definition) | That type for the measure's committed, digital sentences | US$0.002 per type |
| `second.context_chars` and `ask.context` | The questions that carry the paragraph (the longer paragraphs only, for `second.context_chars`) | up to US$3 and US$6 to 9 |
| A threshold inside the asked range (`band.low`, `band.keep`, `band.context`, `band.second_grade`, `digital.floor` down to `digital.ask_low`, `link.asset_yes`, `link.asset_band_low`, `link.strong_yes`, `asset.floor`, `floor.types`, `types.multiple`, `posterior.*`, `dashboard.*`) | Nothing: `./wbg_portfolio publish` rereads the stored scores | free |
| A threshold outside the asked range (`band.ask_low` or `digital.ask_low` lowered, `link.asset_ask_low` lowered, `asset.floor` lowered) | Only the pairs newly let in: about US$0.11 per 1,000 measure pairs, US$0.08 per 1,000 package pairs | US$0.5 to 4 |

What else is free or cheap. A set whose questions, gates, patterns, search run and round settings match its last finished run is skipped whole (`wbg.funnel_state` holds the fingerprint). A phrase or pattern edit asks only the sentences it newly admits. A gate, an ask floor (`floor.ask`, `asset.ask_floor`) or a round setting asks only the sentences newly let in; the feedback rounds run only when round 0 asked something new. A measure of another family changes nothing here: a measure's own questions hold only its own definition; what every family shares is the scale, the asset map, `ask.context`, `floor.ask` and the model. A new document asks its own sentences only. The code of a corpus stage runs that stage and what follows again at no model cost (`clean` reads every PDF again: hours). The order of rows in a file never changes a question: the assets and kinds a question lists are sorted by id. Setting a reply or a request knob (`second.max_tokens`, `second.reasoning`) asks nothing again but changes the answers still to come, so fix them with the model.

**What the question rewrite of 2026-11-21 re-asks.** The digital, asset, paragraph-note and climate governance wording, the asset definitions, the taxonomy scale and the new package steps change these questions, so the next run asks them again (from the code, and the counts published on 2026-11-21: about 141,917 sentences in scope, 12,222 asked the digital question, about 17,000 band pairs with a context score, 5,229 asset second looks, 13,993 packages). The decision model's questions go 50 to a request (`ask.batch_items`); the chat model's one to a request. Run `./wbg_portfolio run --from classify --dry-run` first: each step's line gives its stored answers and its requests.

| Step | Why it asks again | About |
|---|---|---|
| classify (asset first pass) | new definitions, the shared rule, the new scale, a new subject line | every sentence the asset funnel lets in, for each asset: the gate and patterns over the 141,917 sentences in scope, plus feedback rounds; on the order of 100,000 to 200,000 questions, 2,000 to 4,000 requests; the asset search runs again first |
| measure: climate_governance first pass | new definition | the sentences its gate and patterns let in (a few thousand), and its search runs again |
| measure: digital | new question and scale | about 12,222 sentences (more if the kept set grows), about 250 requests |
| measure: context looks (every measure) | new paragraph note | about 17,000 band pairs, about 350 requests; climate_governance's chat grades too (new definition) |
| measure: asset second look | new question | about 5,229 kept sentences, one chat request each |
| measure: asset types | new question, and now asked for second-look assets too | about 4,400 sentence x asset pairs (the 5,243 kept sentences held about 4,400 asset rows), one chat request each |
| measure: policy, measure types | unchanged | only sentences newly kept |
| link: package asset first pass | new question and scale, new definitions | 13,993 packages x 4 assets, about 56,000 questions, about 1,100 requests |
| link: package asset second look | the asset definitions it shows changed | every package x asset scored from `link.asset_ask_low` (0.3) up, on the new scale: a few thousand chat requests |
| link: package asset types (new) | new step | every package x asset that counts: about 3,000 to 5,000 chat requests (2,833 packages were linked on 2026-11-21) |
| link: strong link (package to measure) | now every package, not only those with an asset | up to 13,993 x 3 likeliest measures, about 42,000 questions less those stored, about 800 requests; climate_governance's are all new (new definition) |
| check grade (the evaluation) | the measure grade now alone, a second request for digital and general aim | one more request per measure item per grader: about 1,250 x 3 |

At the prices measured on 2026-11-20 (decision model about US$0.035 per 1,000 context questions and less for the short first-pass questions; chat model about US$0.08 per 1,000) this is roughly US$2 to 4 for the decision model (the asset first pass is most of it, as in the US$0.8 to 1.2 estimate of 2026-10-22, with longer scales now) and about US$1.5 for the chat model (some 17,000 requests), before the evaluation. The dry run gives the figure for the store as it stands.

Estimates (2026-10-22, 251 projects, about 150,000 searched sentences, starting gates 0.3): embedding about US$1; the asset map US$0.8 to 1.2; twelve measures US$3 to 5; types and package links US$1 to 2; **about US$6 to 9 for a clean run, US$15 to 19 with the second looks and the chat questions**. The dry run gives the figure for the corpus as it stands.

**What a run never loses.** Each answer is committed as it arrives, so a crash, a kill or the spend cap loses nothing already paid for. A batch with one unreadable answer keeps its good ones and the bad one alone is asked again. Identical (state, question) items in one stage are asked once. An empty reply from the chat model is asked again within the run and never counts toward `second.max_attempts`; only a reply with content that cannot be read does. An item the provider refuses alone (an HTTP 4xx for that request, after the run has had answers from the same model) is recorded in `<store>.refused`, skipped, and listed at the end of the run; delete its row there to ask it again. A refusal about the whole request (a bad key, no credit, or nothing answered at all) stops the run.

## Clean run on the box

1. The key: `.secrets/wbg-openrouter-key` in the main checkout, mode 0600, owned by the account that runs the pipeline; or `$WBG_OPENROUTER_KEY`. Postgres: `postgresql:///work` (`$WBG_PG` overrides).
2. `./wbg_portfolio check import`: the grades of the file-based sheets (`data/labels/*_units.csv`, `package_assets.csv`) into `wbg_store.grades`, once.
3. `./wbg_portfolio run --to search --max-spend 2`: fetch, prepare, embed, search (embedding about US$1). The first run after the parser-version change of 2026-10-24 reads every PDF again (hours); embeddings come from the cache where the text is unchanged.
4. `./wbg_portfolio run --from classify --max-spend <US$>`: the asset map and the measures from the store (every answer asked before is reused: the old file store is read once and copied in), the types and links from the store.
5. `./wbg_portfolio check evaluate`: every kind against every grade; the layers and gates fitted and written; `data/reports/evaluate.md` and `wbg.measure_quality`. Set the panel from it; `./wbg_portfolio publish`.
6. Grading rounds: `./wbg_portfolio check draw --seed <n> --design train` writes `data/review/tasks_s<n>.jsonl`; a person or an agent writes `fills_s<n>.jsonl` (`{"task_id", "grade", "rationale"}` per task); `./wbg_portfolio check fill --seed <n> --file fills_s<n>.jsonl --grader <who>`; `./wbg_portfolio check evaluate`; `./wbg_portfolio publish`. `./wbg_portfolio check spotcheck --seed <n> --file fills_s<n>.jsonl` writes a thin workbook for a person to check an agent's fills. An evaluation draw (`--design eval`) is graded afresh and fits nothing; its figures are the ones to quote.

## Adding a measure

A measure is three small files and one command; the only part a person must write is the definition:

- `analysis/labels/<id>.csv`: `measure_id`, `measure_name`, `definition` (three to six sentences: what counts, what does not; name the measure, never the digital asset, the asset map supplies that link), `theme`, `family` (the family it belongs to, one per measure: `climate`, `cloud_data`; see section 5c), `active`, `version`;
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

`--expand` is one generative call (`scratch.GEN_MODEL`, a few cents, stored under its ask so the same ask gives the same files) that writes a name, a definition of 60 to 100 words, 8 to 10 phrases and up to 3 patterns; they are printed, and `--definition`, `--phrase` or `--pattern` given alongside override them. The tally is a first-pass tally only, and says so: a sentence counts in it when its first score alone reaches `floor.default`. It is not the published rule (which also needs the second looks in the band and the digital question), so a measure added from the same files may keep fewer. Trying again with a sharper definition re-asks only the sentences whose question changed. A scratch set graduates when a person copies its files into `analysis/labels/` (a pull request) and runs `./wbg_portfolio add <slug>`; the stored answers are reused.

`./wbg_portfolio status` prints the brief an agent or a person needs at the start of a session (`src/wbg_portfolio/status.py`): the release lines, the active measures, scores per kind, stored answers, grades, fits, spend, the last publish, the scratch measures tried, whether a key is present.

## What the app sees

Schema `wbg` only, through the functions of `src/wbg_portfolio/app.py` (section 5f); the record schema `wbg_store` is for the pipeline and for querying grades and spend. Every count in the findings is the pipeline's decision, not a person's: an evaluation draw's weighted precision and recall (`wbg.measure_quality` with `sheet` = evaluation) is the number to quote beside it.
