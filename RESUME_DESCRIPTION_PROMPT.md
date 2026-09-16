# Résumé description prompt for this project

Paste everything below the line into your résumé-writing assistant. Every fact
in it is measured from this repository; the prompt forbids inventing anything
beyond what it supplies.

Regenerate this file after significant changes: the metrics come from
`python3 evaluate.py --profile example --fixture eval/fixtures/example_postings.jsonl`
and `python3 evaluate.py --profile me --ablation`, and the counts from
`python3 tests.py` and the health check.

---

You are writing résumé bullets for a software project. Use only the facts in
this brief. Do not invent metrics, technologies, company names, dates, team
sizes, or user counts. If a number is not here, it does not exist.

## What the project is

**jobrank** — a recruiting platform that parses a résumé, derives a per-user
profile from it, ingests live job postings from multiple sources, ranks them for
that specific person, and measures the quality of that ranking against labelled
data. Public repository: github.com/anshikhosa-sys/Internship-Tracker
(MIT licensed). Python, Flask, SQLite, ONNX runtime. Solo project.

It began as a single-user internship tracker with scoring constants tuned by
hand to one person, and was rebuilt into a general, profile-driven system where
no weight in the code is specific to any individual.

## Architecture, stated precisely

**Résumé ingestion with structured extraction.** Accepts PDF, DOCX, Markdown and
plain text (pypdf; DOCX parsed from its XML with the standard library). Runs
through one extraction interface with two backends: a locally hosted LLM
(Ollama, constrained to loopback addresses so a résumé can never leave the
machine) and a deterministic rule-based parser used when no model is running.
All model output is validated against a JSON Schema (a ~60-line validator
written for the subset used) with one retry, then falls back to rules. Every
extraction is cached by SHA-256 content hash, so the same résumé or posting is
never analysed twice. Contact details are deliberately never extracted.

**Per-user profile derivation.** Computes, from each user's own evidence:
skills weighted by where they were demonstrated (job bullets > projects >
skills list) and decayed by recency; seniority inferred from dated experience
with internships and part-time work credited at a discount, never self-reported;
role affinity per role family from past titles weighted by tenure blended with
stated targets; and per-factor weights derived from the user's 1–5 priority
ratings.

**Multiplicative multi-factor scoring engine.** `score = 100 × Π factorᵢ ^ wᵢ`
over six factors in [0,1]: skill overlap, seniority fit, role affinity,
preference match (location, remote, company size, industry, start date),
freshness (exponential decay whose half-life varies by employer size), and
semantic similarity. Multiplication is deliberate: a near-zero on any critical
factor sinks the result, where addition would average it away. Weights are
exponents rather than coefficients because in a product a coefficient rescales
every score identically and changes no ordering; an exponent genuinely reorders.
Missing data yields a neutral value, never a penalty.

**Semantic matching layer.** Local ONNX embedding model (BAAI/bge-small-en-v1.5,
384 dimensions, ~67 MB, CPU) over postings and résumé text, stored in SQLite
with exact cosine search in NumPy; hashed-token vectors are the fallback when
the model is unavailable. Layered on top of keyword and structured factors, not
replacing them.

**Evaluation harness with named retrieval metrics.** Graded relevance labels
(0–3) seeded from real applications or added through a labelling CLI. Computes
AUC, MRR, precision@k, recall@k and nDCG@k against a random-order baseline over
the same pool, plus per-factor ablation. `evaluate.py --compare` is a regression
gate that exits non-zero when a tracked metric drops beyond tolerance, and
baselines record a hash of every config file that shapes a score.

**Multi-source ingestion pipeline.** Eight job-listing sources behind one
`Posting` contract, with two-pass deduplication (stable identity hashing plus
URL-and-title matching biased toward under-merging, because a wrong merge hides
a real job invisibly). Two SQLite databases separate rebuildable posting data
from irreplaceable application history.

**Observability.** Structured JSON logging; a compact per-factor decision record
for every posting scored; `run.py --explain <id>` printing the full calculation
for one posting; and a stats view covering extraction usage, cache hit rates,
enrichment and embedding coverage, and ranking timings.

**Application workflow.** Stage tracking (applied, online assessment, interview,
offer, rejected), per-company application quotas grouped by parent company, a
display cap so one employer cannot dominate the list, and locally assembled
copy-paste prompts for cover letters that are constrained to what the parsed
résumé actually contains.

**Zero marginal cost.** No paid API, SDK, or key anywhere in the system; a test
asserts it. Every external dependency degrades rather than failing.

## Measured results — use these numbers, and only these

Reproducible example set (118 hand-assigned labels, 200-posting snapshot):

- AUC **0.875** (random baseline 0.502)
- precision@10 **0.90** (random 0.259)
- precision@20 **0.80** (random 0.247)
- nDCG@10 **0.598** (random 0.178); nDCG@20 **0.697** (random 0.197)

Real-world set (36 genuine applications as positive labels, 4,130 live postings):

- AUC **0.771** (random 0.494)
- recall@500 **0.333** (random 0.112) — 3× better than chance
- mean rank of a relevant posting **956 of 4,130** (random 2,091) — 2.2× better

Factor ablation on the real set (AUC lost when each factor is removed):

- semantic similarity **−0.061** (largest single contributor)
- role affinity **−0.048**
- seniority **−0.007**, preference match **−0.007**

## Scale and engineering facts

- **8** job-listing sources; **8,400+** rows fetched per run, deduplicated to
  **4,100+** active postings
- **~14,000** lines of Python
- **187** automated tests plus **26** browser checks driving real Chromium
- Full pipeline run — fetch, deduplicate, enrich, score, store — in **~13
  seconds**; re-scoring all 4,100 postings in **~1 second**
- Performance work: a cold `--explain` went from **over 5 minutes to ~3
  seconds** after removing a per-start network check and adding caching;
  scoring itself went from 8.3 s to ~1 s via a substring pre-filter before regex
  matching and memoising posting-level facts

## Engineering judgment worth showing

- The evaluation harness caught a real modelling flaw: role families adjacent to
  a user's stated targets were treated as unwanted, burying genuine applications
  around rank 2,000 of 4,165. Fixing it raised AUC from 0.763 to 0.783, measured.
- Vetoing non-software disciplines in title classification (a "Water
  Infrastructure Engineering Intern" is not an infrastructure engineering role)
  raised example-set AUC 0.852 → 0.875 and nDCG@20 0.575 → 0.697.
- Known limitations are documented rather than hidden: positive-only labels make
  precision a lower bound, applications carry selection bias, and title-only
  postings impose a ceiling.

## How to write the bullets

- Formula for every bullet: **strong verb → what was built → specific
  technology → quantified result.**
- Lead with the AI/ML systems work: structured LLM extraction with schema
  validation and caching, embedding-based semantic retrieval, a multi-factor
  ranking model, and an evaluation harness with named IR metrics.
- Frame this as **AI engineering and systems work** — information retrieval,
  ranking, evaluation methodology, pipeline design — **not** as a personal
  productivity script or a job-application helper.
- Prefer the measured comparisons (AUC versus a random baseline, ablation
  deltas, latency reductions) over adjectives.
- Do not claim users, traffic, revenue, team leadership, or production
  deployment. None of those are established here.
- Produce 4–6 bullets for a technical résumé, then 2 shorter variants of the
  strongest bullet for space-constrained formats.
