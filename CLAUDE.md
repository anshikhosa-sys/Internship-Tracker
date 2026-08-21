# Internship Finder — Project Spec

This file is the source of truth for what this project does.
(Claude Code reads it automatically when working in this repo.)

## Goal

A local tool that fetches internship postings and ranks them by fit against a
configurable profile, so the best-matched roles sort to the top instead of
appearing in arbitrary order.

## Data source

The [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
GitHub repo — README-based, updated daily, organized by category (Software
Engineering, Product Management, Data Science/AI, Quantitative Finance,
Hardware Engineering).

Summer 2027 is the active cycle. Summer 2026 is archived.

## Scoring model

Postings are scored by a weighted, tiered keyword model. Every keyword and
weight lives in `config.py`; no scoring values appear anywhere else in the
codebase, so tuning never requires editing logic.

A posting's score is the sum of:

1. **Role tier** — the heaviest component. Tiers are keyword sets with point
   values; a posting matching several tiers takes only the highest-valued one,
   so a padded title can't collect points from multiple tiers at once.
2. **Category bonus** — which section of the source it was listed under.
3. **Focus bonuses** — topic keywords, which stack but are capped by
   `MAX_FOCUS_BONUS` so a keyword-stuffed title can't overturn the tiers.
4. **Out-of-scope adjustments** — negative values for fields the search isn't
   covering, so they sort to the bottom rather than being hidden.

Every posting retains a breakdown of which rules fired and for how many points,
surfaced in the dashboard so a score is never an unexplained number.

## Core features

- Fetch and parse listings into: company, role title, category, location, link,
  date posted.
- Score and rank postings, highest fit first.
- Internship-level filtering.
- Flag postings that are new since the previous run.
- A local Flask dashboard showing ranked postings with fit score, company,
  role, location, and apply link, plus an applied-to tracker.

## Tech choices

- Python for fetching/parsing/scoring; `requests` for HTTP.
- SQLite for local storage (no database server).
- Flask for the local dashboard.
- Heavily commented, with design decisions documented inline.

## Constraints

- Runs locally, no hosting. Single user, no login.
- Reliable core first; structured so more sources can be added later
  (e.g. `SimplifyJobs/New-Grad-Positions`).

---

## Decisions made during the build

- **Location does not affect the fit score.** Ranking is on role fit alone.
  Location is parsed and displayed but carries no weight.
- **Quantitative Finance and Hardware Engineering are filtered out at parse
  time.** Only Software Engineering, Product Management, and Data Science/AI
  are ingested. One-line change — see `INGEST_CATEGORIES` in `config.py`.
- **Tier values are chosen to guarantee ordering, not just suggest it.** The
  top tier is weighted so its worst-case score still exceeds the best case of
  the tier below, since category and focus bonuses would otherwise let a lower
  tier overtake a higher one. See the comment on the first entry in
  `ROLE_TIERS`.

## Notes about the data source

Learned by inspecting it; worth remembering if the upstream repo changes:

1. **Listings are HTML `<table>` blocks inside the README, not markdown
   tables.** A markdown-pipe regex returns zero rows.
2. **Continuation rows use `↳` as the company name**, meaning "same company as
   the row above." The parser carries the last real company forward.
3. **There is no date-posted column — only a relative "Age"** (`18d`, `1mo`).
   Date posted is therefore derived and approximate. The new-posting flag uses
   the tool's own `first_seen` timestamp instead, which is exact.
4. **Closed roles are already excluded upstream** — they move to a separate
   `README-Inactive.md`.
5. **The repo's legend documents 🛂 and 🇺🇸 markers, but no row actually uses
   them.** Those fields parse to False from this source; they're kept on the
   `Posting` model for future sources.
