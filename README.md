# wbg-digital-resilience

Finds climate resilience commitments for digital infrastructure in World Bank appraisal documents, and links them to upcoming procurement.

```
./run.sh              # fetch, clean, audit, load into Postgres, write review sheets
./run.sh --limit 3    # the same on three projects
python -m pytest      # tests (locally)
```

Data goes to the main checkout's `data/` folder (never committed). The cleaned tables also go to Postgres, schema `wbg`. The review sheets for reading the cleaning by eye are in `data/review/`.

Start with `AGENTS.md`, then `docs/methodology.md`.
