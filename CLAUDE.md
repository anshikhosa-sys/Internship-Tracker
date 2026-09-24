# jobrank — working rules

A local recruiting platform: parse a résumé, derive a per-user profile, ingest
postings from many sources, and rank which roles that user should apply to.
Design history lives in `docs/design-notes.md`; read it only when a rule below
needs its backstory.

## Commands

```
.venv/bin/python tests.py            # unit + integration suite (pytest under the hood)
.venv/bin/python browser_tests.py    # real Chromium through the UI; required for template JS changes
.venv/bin/python evaluate.py --profile example           # ranking metrics on the golden set
.venv/bin/python evaluate.py --profile example --compare # regression gate vs saved baseline
.venv/bin/python run.py --help       # refresh, profile, explain, apply, label, stats
.venv/bin/python run.py describe     # fetch real job descriptions from job boards
.venv/bin/python run.py market --profile me   # what the market wants, and your gaps
.venv/bin/python app.py              # dashboard on http://127.0.0.1:5000
.venv/bin/python healthcheck.py      # every subsystem, PASS/WARN/FAIL
```

Run BOTH test suites after any change, and the eval gate after any scoring change.

## Layout

```
app.py refresh.py selfcheck.py   LaunchAgent entry points — thin shims, never move them
run.py evaluate.py healthcheck.py  CLIs
jobrank/
  config/        DATA ONLY: weights, vocabularies, company groups, limits
  models.py      Posting, ParsedResume, Preferences, Profile dataclasses
  textmatch.py   whole-word matching, normalization (the only place regexes are built)
  llm/           backend interface, local Ollama backend, rule-based fallback, cache + usage
  resume/        PDF/DOCX/TXT readers, structured extraction
  profile/       preference intake, profile derivation, profile store
  scoring/       factor functions, multiplicative engine, explanations
  semantic/      local embeddings + SQLite vector store
  descriptions.py fetch a posting's real description from its job board (free APIs)
  market.py      what the live corpus demands: skill rarity, per-family demand
  enrich.py      optional posting enrichment (cached by content hash)
  ingest/        Source interface, registry, async pipeline, fuzzy dedupe, sources/
  eval/          golden labels, retrieval metrics, harness
  workflow/      application state machine, quotas, grounded résumé tailoring
  validate.py    startup config validation
  storage.py     SQLite: postings.db (rebuildable) and applications.db (irreplaceable)
  web/           Flask app, templates, static
  ops/           healthcheck, selfcheck, notifications
profiles/        per-user JSON; only example.json is committed
eval/            golden/ labels and baselines/; only example files are committed
docs/            design notes and history
```

## Architecture invariants

1. **Config is data, logic is code.** No number, keyword, or company name in
   `scoring/`, `profile/`, or `workflow/`. If you need one, add it to
   `jobrank/config/` and let `validate.py` check it.
2. **Nothing is tuned to one person.** Anything that only makes sense for one
   user is derived from their profile, never written as a constant.
3. **Factors multiply.** `score = Π factorᵢ^wᵢ × 100`, each factor in [0, 1].
   A near-zero on a critical factor must sink the result; addition averages it
   away. Exponents (wᵢ) are global and fitted on the golden set, never stated
   by a user — a priority rating is a preference wearing a weight's clothes.
4. **Preferences filter; they never score.** Nothing the user *says they want*
   may reach a factor. Scoring answers "is this application worth making",
   which depends on evidence and on the market, not on a wish. Stated targets
   are recorded for filtering only. A test asserts two profiles differing only
   in preferences score identically.
5. **Role affinity is evidence, then market transfer.** What the résumé proves
   leads; what the corpus says a family demands fills the gap. Hand-written
   evidence lists are a starting point, never the only say.
6. **Missing data is neutral, not negative.** A posting with no listed skills
   or pay gets the configured neutral value, never a penalty.
7. **Keyword and semantic matching are layered.** Keywords catch hard
   requirements, embeddings catch paraphrase. Never replace one with the other.
8. **Zero cost, always.** No paid API, SDK, or key anywhere. LLM work goes
   through `jobrank/llm`, whose backends are local-only (Ollama) or rule-based.
   A test asserts this.
9. **Every external dependency degrades.** No Ollama → rule-based extraction.
   No embedding model → hashed-token vectors. Never hard-fail on either.
10. **LLM output is structured and cached.** JSON-schema validated, keyed by a
   content hash. The same résumé or posting is never analyzed twice.
11. **Whole-word matching, never substrings**, and context where a skill's
    name is an ordinary word. "ai" is inside "maintain", "exa" inside "Texas",
    and "Spring 2027" is a season — that one credited 153 live postings with
    the Java framework. Use `textmatch`; ambiguous names go in
    `taxonomy.AMBIGUOUS_SKILLS`.
12. **Posting identity is hash(normalized company, normalized role).** It files
    the user's applications. Changing it orphans them silently.
13. **Dedupe under-merges.** A wrong merge hides a real job invisibly; a missed
    merge shows a visible duplicate. Merge only on same company AND high title
    similarity AND no conflicting discriminator (C++ vs Python, I vs II).
14. **Freshness is never stored.** It changes daily; compute at read time.
15. **Two databases.** Postings are rebuildable; applications are not. Never
    write application state into the postings DB.
16. **Application state moves only through legal transitions**, each
    timestamped. No Discovered → Offer.
17. **Tailoring never invents.** Suggestions are validated against the parsed
    résumé; any technology, number, or employer not in it is rejected.
18. **A display cutoff is never a deletion.** Filters hide; an override shows.
19. **Probes don't mutate.** Health checks hit `/healthz`, never `/`, which
    registers a visit and clears NEW badges.
20. **A filter's count equals what it renders.** Counts and lists share code.

## Conventions

- Python 3.12+, type hints on public functions, dataclasses for records.
- Logging via `jobrank.log.get_logger(__name__)`; structured JSON lines to
  `logs/`. No `print` outside CLI entry points.
- Module docstrings explain *why*; comments only for non-obvious constraints.
- Tests: `tests/test_<subsystem>.py`, pytest style, no network (sources are
  tested against fixture HTML/markdown in `tests/fixtures/`).
- Template JavaScript changes must pass `browser_tests.py`. Copying uses a
  `<textarea>` + `execCommand('copy')`; see design notes for why.
- Flask `Response(content_type=...)`, not `mimetype=` (double charset).
- A test that cannot run must say so and exit non-zero, never report PASS.

## Adding things

- **Source:** subclass `jobrank.ingest.base.Source` in `jobrank/ingest/sources/`,
  implement `urls` and `parse(text)`, add it to `jobrank/config/sources.py`.
  No pipeline edits.
- **Scoring factor:** a pure function in `scoring/factors.py` returning
  `FactorResult(value, reasons)`, an exponent in `config/scoring.py`, a
  golden-set comparison with `evaluate.py --compare` before merging. It must
  read evidence or the market — never a stated preference.
- **Description source:** a vendor entry in `config/descriptions.py` with a
  public, unauthenticated endpoint. Prefer a board-level one: it serves a whole
  employer in a single request.
- **Company grouping or quota:** `config/companies.py` only.

## Never commit

Real profiles, résumés, golden labels, baselines derived from real
applications, `*.db`, `logs/`, `.cache/`, `letters/`, `.env*`.
