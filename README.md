# jobrank

A local recruiting platform that reads a résumé, derives a profile from it, and
ranks live job postings by how worth applying to they are **for that person,
today** — with a measurable answer for whether the ranking is any good.

![The ranked list, with one posting's score broken down by factor](docs/dashboard.png)

Every number on that card is explained: each factor's value, the exponent the
user's own priorities gave it, and what it multiplied the score by.

---

## The problem, and why ranking is the hard part

Aggregated internship lists are long (this one indexes **4,100+ active postings
from 8 sources**) and undifferentiated. Filtering by keyword returns hundreds of
rows; sorting by date ignores fit. The useful question is *which of these is
worth an application from me, this morning* — and that depends on the person.

The earlier version of this project answered it with constants tuned to one
individual. This version derives everything from whoever uploads a résumé, and
**measures the ranking against labelled data** rather than trusting it.

## How it works

```
                                       ┌──────────────────────────┐
  résumé (PDF/DOCX/TXT) ──► extract ──►│ derived profile          │
  stated preferences ─────────────────►│  skills · seniority      │
                                       │  role affinity · weights │
                                       └────────────┬─────────────┘
  8 job sources ──► parse ──► dedupe ──► enrich ──► │ ──► score ──► rank ──► dashboard
                                          (cached)  │        │
                                                    │        └──► logs/scoring.jsonl
                                     embeddings ────┘             (per-factor record)
                                    (local ONNX)
                                                          golden labels ──► evaluate.py
```

**Extraction** (`jobrank/resume`, `jobrank/enrich.py`) turns documents into
structured JSON through one interface with two backends: a local Ollama model
when one is running, and a deterministic rule-based parser otherwise. Output is
JSON-schema validated and cached by content hash, so the same résumé or posting
is never analysed twice. Contact details are never extracted.

**Profile derivation** (`jobrank/profile`) computes what the scorer reads:
skills weighted by where they were demonstrated and how recently, seniority from
dated experience (internships and part-time credited at a discount, never
self-reported), role affinity from past titles and project evidence blended with
stated targets, and per-factor weights from the user's 1–5 priority ratings.

**Scoring** (`jobrank/scoring`) composes six factors multiplicatively.

**Evaluation** (`jobrank/eval`, `evaluate.py`) scores the current configuration
against graded relevance labels and fails a regression gate.

## The scoring model

```
score = 100 × Π factorᵢ ^ wᵢ        factorᵢ ∈ [0, 1]
```

| Factor | Question | Neutral when |
|---|---|---|
| skills | Does the posting want what the résumé proves? | the posting names no skills |
| seniority | Is this the right level, and is the user eligible? | the posting states no level |
| role | Is this the kind of work the user wants? | the title matches no family |
| preferences | Location, remote, company size, industry, start date | nothing stated to check |
| freshness | Is it still open? Half-life varies by employer size | the posting has no date |
| semantic | Résumé-to-posting similarity, local embeddings | no embedding available |

**Why multiply rather than add.** An application is worth making only when every
condition holds at once. Addition averages a fatal flaw away: a role three
levels too senior still collects most of its points from a strong title match.
Multiplication lets a near-zero on any critical factor sink the result. The
first version of this project *added* a large constant for a wanted title, and
the top of the list filled with month-old postings that could not be won.

**Why weights are exponents.** In a product, a coefficient does nothing — it
scales every score by the same ratio and reorders nothing. An exponent below 1
compresses a factor toward 1 so it still moves the result but cannot dominate;
above 1 sharpens it. A user who rates freshness 5 and role 2 gets a genuinely
different order, with no per-person constant anywhere in the code.

**Why not a learned ranker.** There is no per-user training data, and a
recruiting tool has to explain itself. Every factor here is inspectable
(`run.py --explain`), and every change is measured before it ships. A learned
model is the right tool once labels exist at scale; this structure produces the
features it would use.

**Missing data is neutral, never negative.** A posting that lists no skills is
not a posting that wants skills the user lacks.

## Does it work? Measured, not asserted

`evaluate.py` ranks a labelled pool and reports metrics beside a **random-order
baseline over the same pool**, because a metric without one means nothing.

Committed example set — a fictional infrastructure-leaning student, 118 hand
labels over a 200-posting public snapshot, reproducible on a fresh clone:

```
$ python3 evaluate.py --profile example --fixture eval/fixtures/example_postings.jsonl
```

| Metric | Ranking | Random | 
|---|---|---|
| AUC | **0.875** | 0.502 |
| precision@10 | **0.900** | 0.259 |
| precision@20 | **0.800** | 0.247 |
| nDCG@10 | **0.598** | 0.178 |
| nDCG@20 | **0.697** | 0.197 |

Real-world set — 36 genuine applications as positive labels over 4,130 live
postings (the labels themselves stay private):

| Metric | Ranking | Random |
|---|---|---|
| AUC | **0.771** | 0.494 |
| recall@500 | **0.333** | 0.112 |
| mean rank of a relevant posting | **956** of 4,130 | 2,091 |

**Ablation** — AUC lost when each factor is removed, on the real set:

| Removed | AUC | Δ |
|---|---|---|
| (none) | 0.771 | — |
| semantic | 0.710 | **+0.061** |
| role affinity | 0.723 | **+0.048** |
| seniority | 0.764 | +0.007 |
| preferences | 0.764 | +0.007 |
| skills | 0.785 | −0.014 |

The semantic layer is the largest single contributor, which is the case for
layering embeddings on top of keyword matching rather than replacing it. Skill
overlap is within noise **on title-only data** — most sources publish no
description, so there is usually nothing to match against; it earns its keep
when a description is present.

### Honest limitations

- **Positive-only labels.** Applications say what was relevant, never what was
  irrelevant, so precision@k on the real set is a lower bound and AUC is the
  honest headline. `run.py label queue` labels the top of the current ranking,
  which is what turns precision into a measurement.
- **Selection bias.** Those applications were chosen while browsing an earlier
  ranking, so they over-represent what that ranking surfaced.
- **A ceiling from the data.** ~1,000 postings are some variant of "Software
  Engineer Intern" with no description. Nothing in the model can separate them,
  and the numbers above reflect that honestly.
- **The example labels are synthetic**, assigned to a fictional profile to make
  the harness reproducible. They are not a user study.

## Costs nothing to run

No paid API, SDK, or key anywhere — a test asserts it. The LLM backend is a
**loopback-only** Ollama server (a non-local host raises at call time rather
than quietly sending a résumé over the network); without it, rule-based
extraction runs. Embeddings are a 67 MB ONNX model on CPU, with hashed-token
vectors as the fallback. Every external dependency degrades instead of failing.

## Getting started

```bash
git clone https://github.com/anshikhosa-sys/Internship-Tracker.git jobrank
cd jobrank
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python run.py profile create --id me --resume /path/to/resume.pdf
.venv/bin/python refresh.py          # fetch, dedupe, enrich, score
.venv/bin/python app.py              # dashboard on http://127.0.0.1:5000
```

Everything else:

```bash
python3 run.py --explain <posting_id>   # why this posting scored what it did
python3 run.py --stats                  # extraction usage, cache hits, coverage, timings
python3 run.py label seed --profile me  # golden labels from your own applications
python3 evaluate.py --profile me --compare   # regression gate: exits 1 on a drop
python3 healthcheck.py                  # every subsystem, PASS/WARN/FAIL
python3 tests.py && python3 browser_tests.py
```

Optional local model: `ollama pull llama3.1:8b` — extraction upgrades itself and
nothing else changes.

## Testing

**187 tests** across extraction, profile derivation, every scoring factor, the
evaluation harness, storage and migrations, dedupe, the dashboard, and résumé
upload — plus **26 browser checks** driving real Chromium, because two
clipboard bugs once shipped while every Python test passed. Tests never touch
the real database, profiles, caches, logs, or the network.

## What would change at 100× scale

- **Vector search.** Exact cosine over a few thousand vectors is one matrix
  multiply (~1 ms) and beats an approximate index on recall. Past ~10⁶ vectors
  this becomes FAISS (IVF/HNSW) behind the same `upsert`/`similarities` seam.
- **Scoring.** Python-per-posting is fine at 4k (about a second, cached per
  profile and data version). At millions it becomes a two-stage retrieve-then-
  rank: ANN candidate generation, then full scoring on the top few hundred.
- **Storage.** SQLite with two files (postings rebuildable, applications
  irreplaceable) is right for one machine; multi-user hosting means Postgres
  and a job queue for ingestion.
- **Labels.** With enough of them per user, the hand-composed factors become
  features for a learned ranker — and the harness here is what would prove it
  better.

## Repository layout

```
jobrank/
  config/     data only: taxonomy, company facts, scoring shape, settings
  resume/     PDF/DOCX/text readers, date parsing, rule-based extraction
  profile/    preference intake, derivation, storage, résumé diffing
  scoring/    factor functions, multiplicative engine, explanations
  semantic/   local embeddings, SQLite vector store
  eval/       golden labels, retrieval metrics, harness and gate
  sources/    one module per job source, behind a shared Posting contract
  web/        Flask dashboard, profile intake, stats
  ops/        healthcheck, weekly self-check, notifications, stats
```

Design history and the reasoning behind specific decisions:
[docs/design-notes.md](docs/design-notes.md). Working rules for contributors:
[CLAUDE.md](CLAUDE.md).

## License

MIT — see [LICENSE](LICENSE).
