# wbg-digital-resilience

Finds climate resilience commitments for digital infrastructure in World Bank appraisal documents, and links them to upcoming procurement.

```
./run.sh              # data layout, fetch, clean, components, audit, procurement, load into Postgres, review sheets, run report
./run.sh --limit 3    # fetch only the first three projects; every later stage still runs on all, from cache
python -m pytest      # tests (locally)
```

Data goes to the main checkout's `data/` folder (never committed), laid out as `src/paths.py` describes. The cleaned tables also go to Postgres, schema `wbg`. The review sheets for reading the cleaning by eye are in `data/review/`.

Start with `AGENTS.md`, then `docs/methodology.md`, which lists every step the pipeline takes.
