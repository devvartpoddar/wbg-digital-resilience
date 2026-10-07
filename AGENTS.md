# AGENTS.md

**Read this before writing code here. Read `docs/methodology.md` before changing what the pipeline does.**

## What this is

The pipeline reads World Bank Project Appraisal Documents (PADs) and procurement records, finds which digital assets a project finances and which climate resilience measures it commits to for them, and links that to upcoming procurement. The output helps an advisor decide which Task Team Leaders (TTLs, the staff leading each operation) to talk to, and when. Anything outside the four questions in `docs/methodology.md` does not belong here.

**Current focus: cleaning and paragraph splitting, for both corpora.** Nothing downstream is worth building until a person has read the cleaned text and accepted it.

## Where it runs

**One box, one folder.** Everything lives in `/ygg/projects/wbg-digital-resilience/`. Do not read or write anywhere else on the box.

- **Code**: the main checkout is `/ygg/projects/wbg-digital-resilience`. Board cards work in `.worktrees/<task-id>` inside it, and that folder is deleted when the card completes.
- **Data**: always the main checkout's `data/`, never a worktree's. `src/paths.py` resolves this through git, so every script does the right thing from a worktree. Do not hard-code another data path. `WBG_DATA` overrides it for tests.
- **Python**: `/opt/yggdrasil/venvs/work/bin/python`. Do not pip-install. If a package is missing, say so; adding one is a change to the yggdrasil repo.
- **Postgres**: schema `wbg` in database `work` (`postgresql:///work`, no password). `WBG_PG` overrides it.
- **Hardware**: an Intel N150, four efficiency cores, about 15 GB of memory, no graphics card. Stream large files rather than loading them whole. Anything that needs a graphics card is the wrong design here.

Run everything with `./run.sh`; a smoke run is `./run.sh --limit 3`.

## The principle

Every decision the pipeline makes must be **repeatable** (same text, same answer), **scored** (a probability with a chosen cut-off) and **checkable** (measured against examples a person checked). A model that meets all three may be used, including an external one, provided its exact version is pinned and every response is stored on the box and re-read on re-runs rather than requested again. See `docs/methodology.md`.

## Rules

1. **Never delete data to save space, and never treat a stage's input as disposable.** If disk is a problem, say so.
2. **No document text in git.** Tables carry identifiers, offsets and checksums. Text lives in `data/` and in Postgres, on the box. Test fixtures are invented.
3. **Hand-maintained inputs are read, never written.** `inputs/config/` and `inputs/taxonomy/` are kept by people. Changing an asset or measure definition invalidates every label collected under it, so if a task seems to need it, stop and ask.
4. **Secrets come from the environment.** Never in a file, a command line or a log. If you see one, stop, say so and recommend rotation.
5. **Identifiers are deterministic.** A re-run on unchanged input produces byte-identical identifiers and outputs.
6. **Stages are resumable and skip unchanged work**, keyed on content checksums. On this hardware, reprocessing everything is expensive in hours, not just in principle.
7. **The parse must be reproducible.** Identifiers are positional, so pin tool versions and check `text_sha256` on re-parse. A silent boundary shift repoints every label.
8. **Reports go to the notes vault, never the repository.** Run `src/run_report.py`, then copy the note it writes in `data/reports/` to `Projects/WBG Digital Resilience/Reports/` with the notes tools and add a line to that folder's `README.md` index. Git holds code, hand-maintained inputs and docs, nothing else.
9. **Labels change often.** Key them by the SHA-256 of the unit's text plus the name of the label set, never by position alone, and let nothing but evaluation read them.

## Language in anything a person reads

Plain language; expand every abbreviation on first use. No claim beyond what the data supports: documented is not done, nothing is causal, coverage is below 100 per cent. Never characterise a country, government or institution negatively: say what a document records, not how a borrower is doing. No staff names in any committed file.

## Done means

- `python -m pytest` passes (run it locally; the box's worker venv has no pytest).
- The change was run on real data, at least `./run.sh --limit 3`, and the output was looked at.
- Re-running on unchanged input produces identical output; an interrupted run restarts without redoing finished work.
- Nothing under `data/` is staged, and no new table carries document text into git.
- `docs/methodology.md` says what the code now does.

## When unsure

**Say so and stop.** Do not guess at an asset boundary, a measure definition, a threshold or whether a commitment counts. Those are judgements for people. A task that halts with a clear question is a good outcome; a plausible-looking table built on a guess is the worst one.
