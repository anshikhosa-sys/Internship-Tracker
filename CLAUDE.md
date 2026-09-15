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
.venv/bin/python run.py --help       # refresh, profile, prefs, explain, apply, label, stats
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
   away. Exponents (wᵢ) come from the user's stated priorities.
4. **Missing data is neutral, not negative.** A posting with no listed skills
   or pay gets the configured neutral value, never a penalty.
5. **Keyword and semantic matching are layered.** Keywords catch hard
   requirements, embeddings catch paraphrase. Never replace one with the other.
6. **Zero cost, always.** No paid API, SDK, or key anywhere. LLM work goes
   through `jobrank/llm`, whose backends are local-only (Ollama) or rule-based.
   A test asserts this.
7. **Every external dependency degrades.** No Ollama → rule-based extraction.
   No embedding model → hashed-token vectors. Never hard-fail on either.
8. **LLM output is structured and cached.** JSON-schema validated, keyed by a
   content hash. The same résumé or posting is never analyzed twice.
9. **Whole-word matching, never substrings.** "ai" is inside "maintain",
   "exa" inside "Texas". Use `textmatch`, which also handles `c++` and `.net`.
10. **Posting identity is hash(normalized company, normalized role).** It files
    the user's applications. Changing it orphans them silently.
11. **Dedupe under-merges.** A wrong merge hides a real job invisibly; a missed
    merge shows a visible duplicate. Merge only on same company AND high title
    similarity AND no conflicting discriminator (C++ vs Python, I vs II).
12. **Freshness is never stored.** It changes daily; compute at read time.
13. **Two databases.** Postings are rebuildable; applications are not. Never
    write application state into the postings DB.
14. **Application state moves only through legal transitions**, each
    timestamped. No Discovered → Offer.
15. **Tailoring never invents.** Suggestions are validated against the parsed
    résumé; any technology, number, or employer not in it is rejected.
16. **A display cutoff is never a deletion.** Filters hide; an override shows.
17. **Probes don't mutate.** Health checks hit `/healthz`, never `/`, which
    registers a visit and clears NEW badges.
18. **A filter's count equals what it renders.** Counts and lists share code.

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
  `FactorResult(value, reasons)`, a weight in `config/scoring.py`, a golden-set
  comparison with `evaluate.py --compare` before merging.
- **Company grouping or quota:** `config/companies.py` only.

## Never commit

Real profiles, résumés, golden labels, baselines derived from real
applications, `*.db`, `logs/`, `.cache/`, `letters/`, `.env*`.
