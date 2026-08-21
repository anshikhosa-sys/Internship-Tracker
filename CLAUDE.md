# Personalized Internship Finder — Project Spec

This file is the source of truth for what this project is meant to do.
(Claude Code reads this automatically when working in this repo.)

## Goal

A local tool that finds internship postings and ranks them by how well they fit
my profile, so the best-fit roles are at the top — not just generic SWE listings.

## Primary data source

The [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships)
GitHub repo — README-based, updated daily, organized by category (Software
Engineering, Product Management, Data Science/AI, Quantitative Finance,
Hardware Engineering).

Summer 2027 is the active cycle. Summer 2026 is archived.

## My target roles, in priority order

1. Forward-Deployed Engineer / Solutions Engineer
2. Technical Product Manager / APM (the repo has a Product Management category — prioritize it)
3. Software Engineer with a systems / infrastructure / data / AI-adjacent focus
4. Technical consulting / product or tech strategy

## Core features

- Fetch and parse the Summer 2027 listings into: company, role title, category,
  location, link, date posted.
- Score each posting for fit with a weighted framework:
  - Strong weight for title/category matches to my target roles. Keywords:
    "forward deployed", "solutions engineer", "solutions architect",
    "product manager", "APM", "technical consultant", and SWE roles mentioning
    "systems", "infrastructure", "data", "platform", "AI", "ML".
  - Internship-level only.
  - Bonus points for: AI, ML, infrastructure, data, systems, customer-facing, technical.
- Keyword lists and weights live in ONE clearly-labeled config file (`config.py`)
  that I can edit easily.
- Rank postings by fit score, highest first.
- Flag NEW postings since the last run.
- A simple local Flask dashboard showing ranked postings with fit score, company,
  role, location, and apply link. Let me mark ones I've applied to.

## Tech preferences

- Python for fetching/parsing/scoring; `requests` to pull the repo README.
- Local storage in SQLite or JSON (no database server).
- Flask for the local dashboard.
- Heavily commented, with design decisions documented inline.

## Constraints

- Runs locally, no hosting. Single user, no login.
- Build the reliable core first; structure it so more sources can be added later
  (e.g. `SimplifyJobs/New-Grad-Positions` at graduation).

---

## Decisions made during the build

These were open questions in the original spec, resolved during implementation:

- **Location does not affect the fit score.** Ranking is on role fit alone.
  Location is still parsed and displayed so it can be eyeballed.
- **Quantitative Finance and Hardware Engineering are filtered out at parse
  time.** Only Software Engineering, Product Management, and Data Science/AI are
  ingested. This is a one-line change — see `INGEST_CATEGORIES` in `config.py`.

## Notes about the data source (learned by inspecting it)

These shaped the parser and are worth remembering if the upstream repo changes:

1. **Listings are HTML `<table>` blocks inside the README, not markdown tables.**
   A markdown-pipe regex returns zero rows.
2. **Continuation rows use `↳` as the company name**, meaning "same company as
   the row above". The parser carries the last real company forward.
3. **There is no date-posted column — only a relative "Age" column** (`18d`,
   `1mo`). Date posted is therefore *derived* and approximate. The NEW flag uses
   our own `first_seen` timestamp instead, which is exact.
4. **Closed roles are already excluded upstream** — they move to a separate
   `README-Inactive.md`.
