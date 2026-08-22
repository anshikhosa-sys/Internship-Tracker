# Internship Finder — Project Spec

Source of truth for what this project does. (Claude Code reads this
automatically when working in this repo.)

## Goal

A local tool that fetches internship postings and ranks them by how worth
applying to they are **today**, so the top of the list is a set of actions
rather than a wish list.

## Data source

[SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
— README-based, updated daily, organized by category. Summer 2027 is the
active cycle; Summer 2026 is archived.

## Scoring model

Three factors, each 0 to 1, **multiplied**:

```
score = preference × candidacy × freshness × 100
```

| Factor | Question | Source |
|---|---|---|
| preference | Do you want it? | role family + topic keywords |
| candidacy | Would they take you? | what the résumé proves; blockers |
| freshness | Is it still open? | age of the posting |

Every keyword and weight lives in `config.py`; `scorer.py` contains no numbers.

### Why multiplied and not added

The first version **added** a large constant for wanting a role (+150 for
"forward deployed"). Preference then swamped everything, and the top of the
list filled with month-old postings at companies that take a handful of
interns — accurate to the wish list, useless as actions.

Multiplying encodes the real requirement: an application is worth making only
if **all three** hold. A near-zero in any factor sinks the result, which
addition cannot express.

### Calibration notes

- Scores are compressed by multiplying three fractions. A **50 is near the
  top of what exists**, not a mediocre result. Bands were set against the
  measured distribution, not picked as round numbers.
- **Candidacy is weakly informative from a title alone.** Most titles carry no
  skill keywords, so roughly two-thirds of postings sit at the baseline. It
  works at the extremes — blockers sink, stack matches lift. The fix is
  pasting the real job description on the prep page, not a cleverer constant.
- **Freshness is steep, with a hard 7-day cutoff** (`MAX_AGE_DAYS`). Nothing
  is deleted: an override in the dashboard restores every hidden posting. This
  matters because both forward-deployed roles currently listed are ~29 days
  old, so any tight window hides that whole category. Per-cutoff costs are
  tabulated in `config.py`.

## Core features

- Parse listings into company, role, category, location, link, date posted.
- Score and rank, best first.
- Internship-level filtering.
- Flag postings new **since the user last opened the dashboard** — not since
  the last refresh, which would silently stop flagging things after a day away.
- Local Flask dashboard with score breakdown, apply link, and applied tracking.
- Refresh automatically once a day (macOS LaunchAgent, 08:00), with a
  notification when good new matches appear.
- Free copy-paste prompts for cover letters and work-experience fields,
  adapted per role family, accepting a pasted job description.

## Tech choices

- Python; `requests` for HTTP.
- SQLite for local storage — no database server.
- Flask for the dashboard.
- **No paid APIs.** Nothing in this repo can spend money; a test asserts it.
- Heavily commented, with design decisions documented inline.

## Constraints

- Runs locally, no hosting. Single user, no login — binds to `127.0.0.1`.
- Automation is opt-in and self-contained: `scripts/schedule.sh` only touches
  the user's own LaunchAgents folder, needs no admin rights, and never
  modifies the database.
- Structured so more sources can be added: one file in `sources/`, one line in
  `refresh.py`.

---

## Decisions made during the build

- **Location does not affect the score.** Parsed and displayed, but unweighted.
  Worth revisiting now that same-day applying is the priority.
- **Quantitative Finance and Hardware Engineering are not ingested.** See
  `INGEST_CATEGORIES`.
- **Preference and candidacy are stored; freshness is not.** Freshness changes
  daily for the same posting, so a stored score would be wrong by morning. It
  and the final score are recomputed at display time.
- **The advanced-degree penalty lives in candidacy, not preference.** It's a
  statement about eligibility, not desire. Applying it in both would
  double-count.
- **Cover letters are prompts, never submissions.** Application portals
  prohibit automated submission and an application cannot be unsent, so the
  irreversible step stays manual.
- **Prompts adapt per role family.** An FDE reviewer wants evidence of
  customer-facing work; a SWE reviewer wants depth on the hardest system. Same
  résumé, different pitch. See `ROLE_FAMILY_GUIDANCE`.

## Gotchas in the data source

1. **Listings are HTML `<table>` blocks inside the README, not markdown
   tables.** A markdown-pipe regex returns zero rows.
2. **Continuation rows use `↳` as the company name**, meaning "same as above".
   ~230 of them; parsed literally you get hundreds of postings from a company
   called `↳`.
3. **No date-posted column — only a relative "Age"** (`18d`, `1mo`). Dates are
   derived and approximate. The new-posting flag uses our own `first_seen`
   instead, which is exact.
4. **Closed roles are already excluded upstream** (moved to
   `README-Inactive.md`).
5. **The legend documents 🛂 and 🇺🇸 markers, but no row uses them.** Those
   fields parse to False from this source; kept for future sources.

## Filter interaction worth remembering

The steep freshness curve puts every stale posting **below** the low-fit
threshold. So the dashboard's "show them" link must lift *both* the age cutoff
and the low-fit filter — lifting only the age filter appears to do nothing.

## Files that never get committed

`profile.md` (the résumé), `letters/`, `internships.db`, `logs/`, `.venv/`.
The ignore rules were verified before any personal data was written to disk.
