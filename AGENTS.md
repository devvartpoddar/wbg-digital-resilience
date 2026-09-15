# AGENTS.md

**Standing instructions for any agent working in `devvartpoddar/wbg-digital-resilience`. Read this before writing code. Read `docs/business-rules.md` before changing behaviour.**

---

## What this repository is

This pipeline reads World Bank Project Appraisal Documents, finds where they commit to climate resilience measures for digital infrastructure, judges how firm each commitment is, and links those commitments to upcoming procurement. The output helps an advisor decide which task teams to talk to and when.

A Project Appraisal Document (PAD) is the document approved by the Board describing what a lending operation will finance. A Task Team Leader (TTL) is the World Bank staff member leading that operation — the end user of this work.

The four questions the pipeline answers are listed in `docs/business-rules.md` under SC-01. **Nothing outside those four questions belongs in this repository.**

---

## Where this runs, and what that constrains

**Everything runs on a single Intel N150 mini PC: four cores, four threads, no discrete graphics, roughly 6W design power.** All four cores are efficiency cores. This is not a machine that trains or runs large models, and the pipeline is designed around that rather than in spite of it.

What follows from the hardware, and why the design looks the way it does:

- **Classifiers are logistic regression over precomputed embeddings.** Small, fast, auditable, and the coefficients commit as plain text. This is a hardware fit as much as a methodological choice.
- **Embedding is the one stage that may not run on this box.** If it runs locally it is the slow stage; if it runs against an external service the constraint becomes cost and reproducibility instead. Either way: batch conservatively, checkpoint, make it resumable, and treat re-embedding the corpus as something to avoid rather than something routine.
- **Anything requiring a graphics processor is out of scope.** If a design needs one, it is the wrong design for this box — raise it rather than working around it.
- **Assume single-digit gigabytes of working memory, not tens.** Stream large tables rather than loading them whole where the code can reasonably do so.

**Do not write code that assumes a different machine, a cluster, or a cloud runtime.**

---

## The rules that override convenience

**1. No generative model in the decision path. Ever.**
You may be a language model writing this code. No language model may score a row. Detection uses trained classifiers over embeddings; attribution and modality use dependency-parse rules with a small classifier for ambiguous cases. If you find yourself writing a prompt that returns a label, stop — you have misunderstood the design. This is rule GV-10 and it is the constraint the whole pipeline exists to satisfy.

**2. All data stays on the server. This is the point of the setup.**
The complete dataset — raw documents, raw interface responses, embeddings, every intermediate table — lives on the box so any analysis can be re-run immediately without re-fetching anything. Nothing is deleted after a stage completes, no stage treats its input as disposable, and no cleanup step removes intermediates to save space. If disk becomes a real constraint, say so; do not solve it by discarding data.

**3. Version control holds a subset; the server holds everything.**
These are different boundaries and conflating them is the easiest mistake to make here. `data/raw/`, `data/scratch/` and `*.npy` are gitignored — they stay on disk, they just are not committed. Every uncommitted artefact has a row in `meta/` with its address and checksum. If you add a new raw source, add its manifest before you add the fetcher. See `docs/data-model.md` section 1.

**No document text is committed.** Paragraph and clause tables carry identifiers, offsets and checksums, with no text column. Text appears in exactly one committed place: the sample files under `inputs/labels/samples/`. Do not add a text column to any other table, including outputs — evidence is resolved from the corpus on disk at read time.

**4. Never edit the reference files from code. This is the one place where a wrong change is not cheaply reversible.**
Everything under `inputs/taxonomy/`, `inputs/terms/`, `inputs/config/`, the two label files under `inputs/labels/`, and `meta/gold_set.csv` are hand-maintained by people. Scripts read them and never write them. Two exceptions: `src/sample.py` writes `inputs/labels/samples/`, and `src/validate.py` writes `member_count` in `measure_families.csv`.

Everything else here is a derived table: if a run produces something wrong, re-run it. These files are different. Overwriting a measure definition invalidates every label collected under it, and changing the gold set means the system is being scored against a moving reference. Both failures are silent — the file still parses, the pipeline still runs, and the numbers are quietly meaningless. If a task seems to require touching one of these, stop and raise it.

**5. Never redefine an existing measure.**
Adding a measure is cheap. Splitting, merging or narrowing one invalidates every label collected under the old definition. If a task appears to require a redefinition, stop and say so rather than doing it. This is rule GV-01.

**6. Secrets never appear in chat, in command arguments, or in committed files.**
If you encounter one in the repository or in output, stop, say so, and recommend rotation. Credentials for any external service the pipeline calls are read from the environment, never written into a config file or a notebook.

**7. If embeddings are generated by an external service, the vectors it returns are the artefact of record.**
Pin the model name and revision in `data/features/embedding_manifest.json`, store the returned vectors on the box, and never silently re-embed to "refresh" them. An external provider can update or retire a model without notice, and a re-embed under a changed model produces different vectors for identical text — which moves every downstream probability while the input files still look unchanged. Re-embedding is a deliberate version bump with a re-run of validation behind it, never a side effect of a stage that happened to run again.

---

## Before you write anything

**Read `docs/business-rules.md` first.** It holds the data constraints: permitted values for every enumerated column, null rules, keys, foreign keys that must resolve, and cross-field rules. `src/validate.py` checks the data against it. If your change adds a column or a permitted value, it goes in that document in the same change.

**Read `docs/methodology.md` for why.** Several steps have a fixed required output and more than one candidate method, with a named default that is a starting bet rather than a decision. Section 6 lists them. If your task touches one, read it before assuming the default is settled.

**Read `docs/data-model.md` for schemas.** Column names, types, primary keys and lineage are specified there. Do not invent a column. If a new column is genuinely needed, add it to the data model in the same change.

**Grep for the existing convention before inventing one.** This repository is young, so if a pattern exists anywhere — identifier construction, checksum handling, version stamping, CSV writing — reuse it rather than writing a parallel version that then has to be rejected in review.

**Nine verification steps are open.** Section 14 of the methodology lists them — checks nobody has run, each blocking a claim or a design decision. If your task touches one, the check comes first. Results go in `analysis/`, script and output table together.

---

## Conventions

**Identifiers are deterministic, never random.** A re-run on unchanged input produces byte-identical identifiers. Construction rules are in `docs/data-model.md` section 2.

**Every output row carries its provenance:** `run_id`, and whichever of `model_version`, `threshold_version`, `ruleset_version`, `parser_version` applies to the stage. A row that cannot be traced back to the code that produced it is a bug.

**`rule_fired` is mandatory on attribution and modality rows.** It is what makes each rule scoreable separately, which is how weak rules get deleted on evidence rather than on opinion. Never collapse it.

**Idempotency is driven by content checksums.** A re-run after a source revision recomputes only affected rows. If you write a stage that reprocesses everything unconditionally, you have written it wrong — and on this hardware that mistake is expensive in wall-clock time, not just in principle.

**Every stage is independently runnable and resumable.** Given its committed inputs, a stage runs on its own. A stage that dies partway through can be restarted without redoing completed work. A stage that cannot be run without first running four others is too entangled.

**Use ClearNLP-style dependency labels, not Universal Dependencies.** The English spaCy models emit `dobj` and `nsubjpass`, not `obj` and `nsubj:pass`. A pattern copied from Universal Dependencies documentation matches nothing and raises no error — it just returns zero hits.

**`UNATTRIBUTED` is a value, not a null.** Attribution never guesses. Where more than one asset competes and no rule resolves it, write `UNATTRIBUTED` and let it be counted.

**Store all eight asset probabilities per paragraph, including those below threshold.** It is what allows a threshold to move without re-running the model — which on this box matters a great deal.

**Identifiers are positional and text is not committed, so the parse must be reproducible.** Pin the tool and version, record the settings on the document row, and verify `text_sha256` on every re-parse. A silent boundary shift repoints every label in the repository.

---

## Language and tone in anything user-facing

**Plain language. Expand every abbreviation on first use.** The audience includes people who do not work on machine learning and will not read a second paragraph to find out what a gazetteer is.

**No claim beyond what the data supports.** Documented is not done. Nothing here is causal. Coverage is below 100 per cent. These three caveats appear on every output — see rules SC-03, SC-04, SC-05.

**Never characterise a country, government or institution negatively.** The finding is about what a document says, not about whether a borrower is doing well or badly. "The appraisal document does not record a siting commitment for this asset" is correct. Anything implying a judgement about the country is not.

---

## Definition of done

A change is finished when all of these hold:

- The behaviour matches a rule in `docs/business-rules.md`, or the rule was updated in the same change with a stated reason.
- Schemas match `docs/data-model.md`, including new columns.
- Version stamps are written on every output row the change touches.
- The stage is idempotent — running it twice on unchanged input produces identical output.
- The stage is resumable — it can be interrupted and restarted without redoing completed work.
- `src/validate.py` runs clean against `docs/business-rules.md`, and the resulting metrics row is committed. Metrics moving is not a failure; metrics not being recomputed is.
- No intermediate data was deleted to save space.
- Nothing under `data/raw/`, `data/scratch/` or any `*.npy` is staged for commit, and no new table carries a text column.
- No staff names appear in any committed file.

---

## When you are uncertain

**Say so and stop.** Do not guess at a measure definition, an asset boundary, a threshold, or whether a commitment counts. Those are expert judgements and they live with people, not in code. A task that halts with a clear question is a good outcome; a task that guesses and produces a plausible-looking table is the worst one, because nobody catches it until a task team does.
