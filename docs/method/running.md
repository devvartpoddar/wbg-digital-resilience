# Running the pipeline

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 0. Running it

```
./wbg run                                  # fetch > prepare > embed > search > classify > measure > link > publish
./wbg run --from search --measures cra     # a changed measure, from its search to Postgres
./wbg run --only measure --measures a b c  # three measures through the funnel, together
./wbg run --from classify                  # the asset definitions changed: the map and everything after it
./wbg run --to prepare --project P180495   # one project's documents and tables
./wbg run --from classify --max-spend 5    # stop before any request that would pass US$5
./wbg run --dry-run --from classify        # estimates only; nothing asked, nothing written
./wbg add cra                              # the same as run --from search --to measure --measures cra
./wbg publish                              # every decision read again from the scores, then the views
```

| Stage | What it does | Reads | Writes |
|---|---|---|---|
| fetch | the appraisal documents, procurement records and project records the selected projects lack | the Bank's interfaces | `data/raw/` |
| prepare | read the PDFs, clean, sentences, components, procurement tables; load the corpus tables and cut a release when they changed | `data/raw/` | `data/appraisal/`, `data/procurement/`, Postgres corpus tables |
| embed | the sentence vectors, only what the cache lacks | `data/appraisal/` | `data/embeddings/` |
| search | every label set's phrases against the vectors (cosines) | `data/embeddings/`, the label files | `data/search/<set>-<version>/` |
| classify | the asset map: the funnel over the asset set (section 5b) | the search run, the store | `wbg.scores` (kind asset) |
| measure | every measure through the funnel, then its types, then the pair checks across measures (5c, 5d) | the search runs, the store, `wbg.scores` | `wbg.scores` (kinds measure, type, pair) |
| link | which asset each package buys and which commitments it carries out (5e) | `data/procurement/packages.csv`, `wbg.scores` | `wbg.scores` (kinds package_asset, link) |
| publish | every decision table from the scores as the control panel reads them now, then the views (5g) | `wbg.scores`, the panel, the fits | schema `wbg` |
| audit | the parse and procurement audits, only with `--to audit` or `--only audit` | | `data/reports/audit*.txt` |

The model stages run in one process, with one Postgres connection and one reading of the corpus. Each asks only what the store lacks and stops on a failed request; the next run resumes. **Spend.** `--max-spend X` caps what the whole run may spend: the ledger (`wbg_store.spend`) is read before each step and the step stops before a request that would pass what is left; a stop or a refusal still lands in the ledger. `--dry-run` skips fetch and prepare and prints each model stage's estimate (stored answers, requests to make, about how much) without writing anything.

**What a run cost.** The run prints one line per stage when it ends and appends it to `data/reports/run_stages.jsonl`, one line per run; a run that stops on a failed stage records what ran before it. `src/run_report.py` reads the last line, the spend ledger for that run's window (folded to the pipeline stages) and the evaluation's precision and recall per measure, and writes the note a person reads (AGENTS.md rule 8). The note carries numbers only; the reader adds the decisions.

**No option overrides a threshold.** Every gate, floor, depth and round setting is a line of `inputs/config/control_panel.csv` (section 5g); the commands take a selection, `--dry-run`, `--max-spend` and the worker count, nothing else that moves a number.

**Selecting projects.** Filters on the included rows of `inputs/config/cohort.csv`: `--project`; `--country` (code); `--region` (part of the name); `--fy` (approval fiscal year); `--practice` (Global Practice name or code, or part); `--unit` (managing unit or code, or part); `--set` (`digital` or `digital_contributor`). Each takes several values; different filters combine with "and". A project outside the cohort is refused. A run over named projects writes scores for those units only and is not published (set `WBG_PUBLISH_SCOPED=1` for a scratch database). `./wbg list` prints a selection, `./wbg summary` where each project stands.

## What a change costs

Answers are the only thing that costs money. An answer is stored under the model, the exact state sent (the sentence with its lead-in, section and component; a package with its component; a commitment and a package) and the exact question (its wording and scale, not its id). A stage asks only the keys the store lacks. So:

- **Nothing**: a set whose questions, gates, patterns, search run and round settings match its last finished run is skipped whole (`wbg.funnel_state` holds the fingerprint): no store read, no scores rewritten. The pair-check and link stages always cover every active measure, whatever subset a run names, because each replaces its whole kind.
- **A decision floor, `asset.*`, `types.multiple`, `floor.pair_check`, `posterior.*`, `dashboard.*`**: nothing. `./wbg publish` rereads the scores (a minute or two).
- **A definition, a phrase or a pattern edit**: only the sentences whose question changed, or that the new gate or pattern newly admits. `./wbg run --from search --measures <set>`.
- **An ask floor (`floor.ask`, `asset.ask_floor`), a gate, a round setting, `link.*`**: the sentences or pairs newly let in. The feedback rounds run only when round 0 asked something new of a set already scored, so a re-run after a refit does not move the admitted pool.
- **`ask.context`**: every sentence-level question is a new state. A whole-cohort re-ask (about US$6 to 9 at the current corpus and gates). Test it first on the graded sentences.
- **A new document**: its own sentences only.
- **The model snapshot (`questions.MODEL`)**: everything; a deliberate step followed by re-measurement.

Estimates (2026-10-22, 251 projects, about 150,000 searched sentences, starting gates 0.3): embedding about US$1; the asset map US$0.8 to 1.2; twelve measures US$3 to 5; types, pair checks and package links US$1 to 2; **about US$6 to 9 for a clean run**. The dry run gives the figure for the corpus as it stands.

## Clean run on the box

1. The key: `.secrets/wbg-openrouter-key` in the main checkout, mode 0600, owned by the account that runs the pipeline; or `$WBG_OPENROUTER_KEY`. Postgres: `postgresql:///work` (`$WBG_PG` overrides).
2. `./wbg check import`: the grades of the file-based sheets (`data/labels/*_units.csv`, `pairs.csv`, `package_assets.csv`, `package_links.csv`) into `wbg_store.grades`, once.
3. `./wbg run --to search`: fetch, prepare, embed, search. The first run after the parser-version change of 2026-10-24 reads every PDF again (hours); embeddings come from the cache where the text is unchanged.
4. `./wbg run --from classify --max-spend <US$>`: the asset map and the measures from the store (every answer asked before is reused: the old file store is read once and copied in), the types and pair checks re-asked (their state now carries the section and component), the links from the store.
5. `./wbg check evaluate`: every kind against every grade; the layers and gates fitted and written; `data/reports/evaluate.md` and `wbg.measure_quality`. Set the panel from it; `./wbg publish`.
6. Grading rounds: `./wbg check draw --seed <n> --design train` writes `data/review/tasks_s<n>.jsonl`; a person or an agent writes `fills_s<n>.jsonl` (`{"task_id", "grade", "rationale"}` per task); `./wbg check fill --seed <n> --file fills_s<n>.jsonl --grader <who>`; `./wbg check evaluate`; `./wbg publish`. `./wbg check spotcheck --seed <n> --file fills_s<n>.jsonl` writes a thin workbook for a person to check an agent's fills. An evaluation draw (`--design eval`) is graded afresh and fits nothing; its figures are the ones to quote.

## Adding a measure

A measure is three small files and one command; the only part a person must write is the definition:

- `analysis/labels/<id>.csv`: `measure_id`, `measure_name`, `definition` (three to six sentences: what counts, what does not; name the measure, never the digital asset, the asset map supplies that link), `boundary` (optional, read by the pair checks), `theme`, `active`, `version`;
- `<id>_queries.csv`: six to ten search phrases, each a sentence a document might write;
- `<id>_patterns.csv` (optional): one or two whole-word patterns (`\bunderground\b`, not `ducts?`, which also matches "conducted");
- `<id>_types.csv` (optional): the approved types; without one, the first run proposes a list into `data/types/<id>_types.csv` for a person to approve into the labels folder.

The grade scale is shared (`analysis/labels/scale.csv`); a `<id>_scale.csv` beside the set overrides it. Then `./wbg add <id>` (several: `./wbg add a b c`). Stop and ask if the stage's estimate is above US$1 or the search finds under 20 candidates. Merging two types (`merge_into`) or renaming one (`display_name`) is a file edit and a publish; dropping a type or changing its definition re-asks that measure's types.

## Exploring: a scratch measure

`./wbg try <slug>` answers "what is there on X?" before X is a measure. It writes the three label files under `data/scratch/` (outside git), then runs the measures' own search and funnel: the phrases are embedded, the gate and the word patterns choose the sentences, the decision model grades each on the shared scale, the feedback rounds look below the gate, and every answer is stored and reused. The scores land in `wbg.scores` with kind `scratch`, which the publish never reads, so the commitments pages do not move; `wbg.scratch_findings` and `wbg.scratch_summary` (`sql/views/20_explore.sql`) show them; the Explore dashboard that read them is paused (section 5f).

```
./wbg try e_waste --expand "e-waste: collection, recycling or safe disposal of ICT equipment" --max-spend 0.50
./wbg try e_waste --definition "..." --phrase "..." --phrase "..." --pattern '\be-waste\b'
./wbg try e_waste --project P171305           # one project only
./wbg try --list                              # what has been tried, with counts
```

`--expand` is one generative call (`scratch.GEN_MODEL`, a few cents, stored under its ask so the same ask gives the same files) that writes a name, a definition of 60 to 100 words, 8 to 10 phrases and up to 3 patterns; they are printed, and `--definition`, `--phrase` or `--pattern` given alongside override them. A sentence counts at `floor.default`. Trying again with a sharper definition re-asks only the sentences whose question changed. A scratch set graduates when a person copies its files into `analysis/labels/` (a pull request) and runs `./wbg add <slug>`; the stored answers are reused.

`./wbg status` prints the brief an agent or a person needs at the start of a session (`src/status.py`): the release lines, the active measures, scores per kind, stored answers, grades, fits, spend, the last publish, the scratch measures tried, waiting requests, whether a key is present.

## What Metabase sees

Schema `wbg` only; start cards from the views (section 5f). Hide `loads`, `data_release`, `paragraph_projects`, `scores`, `scratch_sets` and `sentence_vectors` in Metabase's table settings; the record schema `wbg_store` is for the pipeline and for querying grades and spend, not for cards. Every count in the findings is the pipeline's decision, not a person's: an evaluation draw's weighted precision and recall (`wbg.measure_quality` with `sheet` = evaluation) is the number to quote beside it.

**Requested measures.** `./wbg requests` runs what is queued in `wbg_requests.measure_requests`, one at a time, under `requests.cap_per_request_usd` and `requests.cap_per_day_usd`, by calling `./wbg add`. A requested measure's files sit in `data/requested_labels/` until a person copies them into `analysis/labels/`. The Postgres role, the Metabase connection and the timer are yggdrasil changes, not this repository's.
