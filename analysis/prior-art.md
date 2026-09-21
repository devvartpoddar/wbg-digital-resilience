# How this problem is solved in the literature, and where that leaves us

Read before choosing a method. Every figure below is from a named source; the
sources are listed at the bottom. Nothing here is an estimate of ours.

## The finding that matters most: no one has enumerated adaptation measures

The MDB Joint Methodology for Tracking Climate Change Adaptation Finance is the
framework this Bank reports against. It defines **three** categories, not a
taxonomy:

- **Type 1 / adapted** — "activities that integrate measures to manage physical
  climate risks and ensure that the project's intended objectives are realised
  despite these risks"
- **Type 2 / shared objectives** — "activities that directly reduce physical
  climate risk and build the adaptive capacity of the system"
- **Type 3 / enabling** — "activities that contribute to reducing the underlying
  causes of vulnerability ... at the systemic level"

Table 1 offers **eight illustrative examples across all sectors**, and the table
is labelled "illustrative". Two of them sit at exactly the granularity we want —
"adjusted design of culverts required in a road project to cope with the
increased risk of extreme rainfall and flooding", "use of conductors with
operating limits at higher temperature thresholds in a transmission line
vulnerable to increased extreme temperatures". **The document contains no
mention of ICT, telecommunications, digital or data-centre infrastructure.**

So there is no authoritative list to classify against. The methodology insists
adaptation is "context- and location-specific" and pushes the judgement down to
the task team. Our problem is genuinely open-set, and that is a fact about the
field rather than a gap in our reading.

## Every published system solves a bounded version of it

| system | unit | labels | hand-labelled | model | reported |
|---|---|---|---|---|---|
| Climate Policy Radar (2404.02822) | paragraph | **3** (Net Zero / Reduction / Other) | 2,610 paragraphs, 3 experts | ClimateBERT fine-tune, multi-label | F1 **0.849** |
| ClimateFinanceBERT (Toetzke et al., Nat. Clim. Chang. 2022) | project title + description | **~10** granular categories | **1,500 projects** | BERT fine-tune | 80,023 of 2.7M classified as climate finance |
| CRS overreporting (2211.16947) | project title + long description | Rio marker (3-valued ordinal) | two expert re-evaluated sets | RoBERTa / BERT / DistilRoBERTa | see label noise below |
| Sietsma / Callaghan / Minx | abstract | screening + topic model | expert interviews (n=26) | structural topic modelling | 62,191 publications mapped |

**All four label sets were fixed in advance.** Climate Policy Radar's three
categories came from Net Zero Tracker and ClimateBERT-NetZero; there is no
report in the paper of a category emerging from annotation. Their own
methodology repository says "we also currently rely on data labelled manually by
domain experts; this limits the pace", and describes no process for expanding a
taxonomy from data. ClimateFinanceBERT's authors add the caveat that "projects
related to climate change adaptation are highly context-specific" and that the
classifier "may misclassify some infrequent and underrepresented types of
projects".

**Nobody in this literature does inductive discovery of measure types.** That is
what we are attempting, and it is why there is no baseline to inherit.

## The hand labels in this field are about 50% self-inconsistent

The CRS paper (2211.16947) found identical project descriptions carrying **Rio
marker 1 in half the cases and 2 in the other half**, and that its own detection
agreed "in 77% of cases" on unique descriptions but "only in 51% of cases
overall". Its Bayesian correction exists precisely to estimate the gap between
two expert annotation schemes on the same text.

**This constrains how we use the portfolio tracker as a gold set.** Scoring
against it is still the right move, but the ceiling on agreement is not 1.0, and
a method scoring 0.6 against a hand-coded set is not necessarily worse than the
humans who coded it. Rank methods against each other on the same sample; do not
set an absolute pass threshold.

## Method families that exist and are not generative

- **Topic modelling with hierarchical reduction.** BERTopic is the maintained
  implementation of embeddings → UMAP → HDBSCAN → c-TF-IDF, with
  `hierarchical_topics`, `reduce_topics` and a `seed_topic_list` that biases the
  topic representation without constraining the clusters. This is what
  `analysis/discovery/discover.py` was, hand-rolled.
- **Pseudo-relevance feedback over dense vectors.** Query with an example, take
  the top-k, recompute the query as the centroid of the confirmed hits, re-query.
  Vector arithmetic; no training, no model in the loop. Studied for dense
  retrievers in 2106.11251 and Sci. Direct 2022. Note that the current
  state-of-the-art variant, ADORE (2606.13905), generates pseudo-passages with an
  LLM — that variant is out under rule GV-10; classical Rocchio-style feedback is
  not.
- **Open-set span extraction.** Open Information Extraction produces
  (subject, relation, object) with no schema fixed in advance — the relation name
  is just the text linking the arguments (Stanford OpenIE; survey C18-1326;
  CompactIE 2022.naacl-main.65). Open-domain aspect extraction (ODAO, KDD 2022)
  does the same for spans using dependency-parse weak labels.
- **Taxonomy induction.** TaxoGen (KDD 2018, 1812.09551) builds a topic taxonomy
  recursively by clustering **terms** with adaptive re-embedding per node, so each
  node is a cluster of coherent terms and children refine parents. Its output
  shape is a hierarchy induced from the corpus rather than a chosen k.
- **Weak supervision.** Snorkel combines deterministic labelling functions with a
  label model that learns each function's accuracy from their agreement pattern.
  **That label model is generative in the statistical sense** — it models the
  distribution of votes given the true label. Whether GV-10 covers it is a
  decision for the project owner, not an inference from the rule's wording.
  Majority vote over the same labelling functions is fully deterministic.

## Sources

- MDB Joint Methodology for Tracking Climate Change Adaptation Finance, 2021
  (EBRD- and ADB-hosted copies of the EIB publication)
- Climate Policy Radar, arXiv:2404.02822, and its published methodology
- Toetzke, Stünzi & Egli, "Consistent and replicable estimation of bilateral
  climate finance", Nature Climate Change 2022 (ClimateFinanceBERT)
- arXiv:2211.16947, text classification with Bayesian correction on the OECD DAC
  Creditor Reporting System
- Sietsma et al., structural topic modelling over adaptation literature;
  "Machine learning evidence map reveals global differences in adaptation action"
- ClimateBERT: domain-adaptive pretraining on 2M+ climate paragraphs
- BERTopic (Grootendorst); TaxoGen arXiv:1812.09551; ODAO KDD 2022;
  OpenIE survey ACL C18-1326; CompactIE NAACL 2022; PRF for dense retrieval
  arXiv:2106.11251
