# Packages to assets and commitments

Part of the method (`docs/methodology.md` is the index). Measured results and trials: `docs/method/history.md`.

## 5e. Packages to assets and to commitments (`src/link_packages.py`, `./wbg link`)

The plan's component column joins a package to the appraisal document only where the plan printed one and the parser matched it (about a fifth of the paragraphs on 2026-10-20), and a package description does not sit near the committed sentences by cosine (0.2 to 0.45, no gap between right and wrong pairs). So the link is made through what both texts name, the asset, and each step is a stored decision-model answer:

1. **Package to asset.** Every package description in scope (placeholders left out) is embedded once and ranked against the asset phrases and definitions; its best `link.asset_top` assets go to the model, one yes/no each ("does the procurement package buy, build, install, connect, operate or design fiber networks? ..."), with the package text and its plan component as the state. A package has an asset at `link.asset_yes`; most of a plan (works, vehicles, audits) has none. The report counts yes answers at the last rank asked: many there means the depth is too small.
2. **Commitment to package by asset.** Each committed unit (`measure.is_committed`) is paired with the packages of its project that share its asset (`assets_yes`, else its linked asset), at the link levels the dashboards count (`dashboard.commitment_link_levels`). Each pair is asked once, with the sentence, the package and both components: would carrying out this package be where the commitment is delivered or applied? Many to many.
3. **Commitment to package by measure** (route `measure`). A package that buys no digital asset (a risk-assessment consultancy, works to raise tower sites) never meets a commitment by asset, so each committed unit is also paired with its project's `link.measure_top` packages nearest the measure's phrases and definition, not already paired; `route`, `measure_rank` and `measure_cosine` are kept.

Every package at `dashboard.package_link_min_probability` counts for a commitment (on the graded link sheet of 2026-10-21, 25 of 26 links above 0.7 were right). Both files store the prior inputs a package has (section 5g); nothing reads them yet.

**Checking.** `./wbg check all` draws 70 commitment and package pairs into the workbook's `package links` tab by band of the link probability, for a person to answer yes, no or unsure; `--evaluate` stores them in `data/labels/package_links.csv` (`wbg.graded_package_links`) and reports weighted precision and recall at each threshold in `data/reports/evaluate_links.md`. Recall is among the pairs asked. `--design floor` draws 30 around the threshold.

**Output.** `data/links/packages-<version>/package_assets.csv` and `commitment_packages.csv`, a manifest and `data/reports/link_packages.txt`. The version is a SHA-256 over `LINK_VERSION`, the model, `link.asset_top`, `link.measure_top`, `link.asset_yes`, the packages table, the asset phrases, the questions and the measure runs; a new measure run is a new link run, rebuilt from the store for every pair seen before. Loaded as `wbg.package_assets` and `wbg.commitment_packages`, read through `package_assets_in_context`, `commitment_packages_in_context` and `packages_with_commitments`.
