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
| preference | Do you want it? | role family + topic keywords + **employer** + pay |
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

### The employer is part of "do you want it"

Everything in preference used to score the **title** and nothing scored the
**employer**, which produced a list topped by AbbVie (pharma), Springs Window
Fashions (blinds), Devon Energy (oil) and Blackstone (private equity), with
TikTok, Microsoft, Replit and Notion below the fold.

Every one of those titles was classified correctly. The flaw was that the
same title means two different jobs depending on who posts it: at a software
company "Application Engineering Intern" builds the product; at a
window-blinds manufacturer it is internal IT. Different work, different
mentorship, different exit options, roughly 2x the pay.

So `EMPLOYER_TIERS` multiplies preference by employer class — frontier/AI
(1.20), big tech (1.12), tech (1.05), unrecognized (0.90), non-tech (0.40).
It lives in preference, not candidacy: AbbVie would probably *take* him, which
is exactly why wanting it is the question that was missing.

Two details that matter:

- **Matching is whole-word, not substring.** "Texas Instruments" contains
  "exa", "Plasma" contains "asm", "Design" contains the quant firm "sig".
  All three filed under the wrong employer before this.
- **A real infrastructure role escapes most of the penalty**
  (`EMPLOYER_PENALTY_EXEMPT_KEYWORDS`). ML-platform work at a bank is still
  real ML-platform work; it is discounted, not buried.

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
- Refresh automatically: three LaunchAgent slots a day, at login, and on
  opening the dashboard with data older than `STALE_DATA_HOURS`. See
  "What actually keeps the data current" below.
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

- **The default sort is the score.** It was `candidacy`, which ranked by
  "would they take you" alone and discarded two of the three factors — the
  landing page opened with a 26 at the top and the 65 below the fold. The
  landing view is the product; it opens on the number the product computes.
- **Some employers cap applications per cycle** (`APPLICATION_LIMITS`).
  TikTok and ByteDance share a pool of 2. The ranked list shows only what
  is LEFT of a quota, not `MAX_PER_COMPANY` — offering three TikTok roles
  when one application remains is three ways to waste the last slot. A
  rejection still spent the slot, so anything with a status counts.
- **One company is capped at `MAX_PER_COMPANY` rows.** TikTok posts 191
  roles and took 7 of the top 20 on score alone. A "just apply" list that is
  mostly one employer is not a list of actions. Display rule only, liftable
  with `allper=1`, and never applied to something already applied to.
- **Pay lifts, never penalizes.** Only ~25% of postings publish a rate, so a
  missing rate means missing data, not bad pay. A range takes its low end.
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
- **Notification delivery cannot be verified in software.** `osascript`
  exits 0 whether macOS shows the banner or silently drops it (Focus, alert
  style None, screen locked). So `notify.send()` promises only that the
  request was accepted, the health check says delivery is uncheckable, and
  `python3 notify.py` exists to test it by eye. The dashboard banner is the
  channel that cannot be suppressed.
- **Cover letters are prompts, never submissions.** Application portals
  prohibit automated submission and an application cannot be unsent, so the
  irreversible step stays manual.
- **Prompts adapt per role family.** An FDE reviewer wants evidence of
  customer-facing work; a SWE reviewer wants depth on the hardest system. Same
  résumé, different pitch. See `ROLE_FAMILY_GUIDANCE`.

## What actually keeps the data current

The schedule is a floor, not a guarantee, and the logs show why: against a
07:30 / 11:30 / 16:30 schedule, real runs fired at 06:40, 10:36 and 15:38.
launchd replays a slot missed while the Mac was **asleep**, but only one
however many were missed, and never replays one missed while it was **off**.

Three mechanisms, in increasing order of how much they actually matter:

1. `StartCalendarInterval` — the three slots. Always lagging.
2. `RunAtLoad` — a refresh at every login. Covers the powered-off case.
3. `_catch_up_if_stale()` in `app.py` — refreshes when you **open the
   dashboard** and the data has aged past `STALE_DATA_HOURS` (3).

The third is the real guarantee: the data is current whenever you are looking
at it, which is the only moment it needs to be. A full refresh of all five
sources takes under a second, so this is not felt as a page delay.

## Why this got buggy, and what changed

Two copy-button bugs shipped in a row. Both were in JavaScript. `tests.py`
passed through both, and had to — it exercises Python, and neither bug was
in Python. Both were also invisible on screen: the button said "Copied"
while the clipboard held mangled text, then nothing at all.

The fix is not more care. It is `browser_tests.py`, which drives a real
Chromium through the real UI and is wired into `healthcheck.py`. It was
validated the only way a regression test can be: the old bug was
reintroduced and the suite failed on it (`0 chars copied`), then reverted.

**Any change to a template's JavaScript must be verified with
`python3 browser_tests.py`.** Server-side tests cannot see this class of
bug, and neither can reading the code.

A related rule, learned the same way: a test that skips itself must not
report PASS. The first version of the prompt-endpoint test read the empty
test database, found no postings, and passed. `browser_tests.py` exits 2
and says so when playwright is missing.

## How the copy button has to work

Measured in a real browser, with no pre-granted clipboard permission —
which is what an ordinary browser does:

| Approach | Result |
|---|---|
| `navigator.clipboard.writeText()`, called synchronously | `NotAllowedError` |
| `writeText()` after an `await fetch()` | fails outright |
| `document.execCommand('copy')` from a selected `<textarea>` | **works** |

So the prompt lives in a `<textarea>`, not a `<pre>`. Two reasons, and both
were bugs:

- `.value` is the raw string the server rendered. `innerText`, which the
  first version used, returns text **as laid out** — it reflows the element
  and is shaped by CSS, and this one sets `max-height`, `overflow-y`,
  `white-space: pre-wrap` and `word-break: break-word`.
- A textarea can be selected and copied with `execCommand`, synchronously,
  with no permission prompt. The second version fetched the text and then
  called `writeText()`, which a browser refuses.

`navigator.clipboard` is still tried as a second attempt, with no `await`
before it so the click's user activation is still live. `/prompts/<id>/<kind>.txt`
remains as the no-JavaScript escape hatch, linked from the page.

The button reports the character count on success and "Nothing to copy" on
an empty clipboard. Both bugs were silent; this one announces itself.

Use `content_type=`, not `mimetype=`, on the Response: `mimetype` appends
its own charset and you get `text/plain; charset=utf-8; charset=utf-8`.

## Sources

Seven lists, merged and deduplicated. Adding one is a file in `sources/`,
an export in `sources/__init__.py`, and a line in `refresh.py`.

Chieler and DereC4 were added when "no new postings are appearing" turned
out to be a supply problem rather than a bug: tracing the pipeline showed
every fresh row the five older sources published was already stored, and
nothing was being lost. Between them the two added ~1,600 company+role
combinations none of the others carried, taking the pool from 736 to 1,729
active postings and the 7-day window from 151 to 496.

Chieler is worth its place twice over: it publishes an exact ISO date per
row, and `dedupe.py` prefers an absolute date over one derived from a
relative age, so it improves postings that came from elsewhere.

`_strip_html()` in `sources/markdown_table.py` unwraps markdown links to
their label. DereC4 puts the apply link inside the ROLE cell, so without
that, job titles arrived as
`[Algorithm Development Engineer Intern](https://analogdevices…` — the URL
became part of the title and reached the scorer, the dashboard and the
letter prompts.

## The same job, worded differently in two lists

`key_for()` matches on (company, role), which misses the case that actually
cost something: Microsoft's "AI Software Engineering Intern - Edge" and "AI
Software Engineer Intern - Edge" had the SAME apply URL, appeared as two
cards, and an application was sent to both — the exact waste
`APPLICATION_LIMITS` exists to prevent.

A second pass merges postings that share an apply URL **and** have
equivalent titles. Both halves are required: a shared URL alone is not
identity, because several employers point every listing at one careers page
— Zipline's "Software Engineer Intern" and "Computational Physics Intern"
share theirs.

**The bias is deliberately toward UNDER-merging.** A wrong merge hides a
real job and does it invisibly; a missed merge shows a duplicate, which is
visible and fixable. That is why there is no "one title contains the other"
rule: it collapsed "Software Engineer Intern, C++" into "…, Python".
`ROLE_MATCH_RATIO` is 0.85, measured against the live data.

`storage.reattach_orphaned_marks()` uses the SAME `dedupe.same_role()`.
It has to — the surviving row keeps one of the two titles, so an
application filed under the other would show as not-applied, and the
obvious next step would be to apply a second time. "Is this the same job"
must be one rule, in one place.

## A filter must never disagree with itself

Two rules the age dropdown now follows, both learned from it being wrong:

- **Every option carries the count it would return** — "Last 3 days (33)".
  An empty result is then visible before you pick it, rather than looking
  like a broken page afterwards.
- **The count must equal what renders.** `_apply_caps()` is shared between
  the list and the counts for exactly this reason; when the count was
  computed before the per-company cap it read higher than the list it
  described. `_age_window_counts()` also has to pass the EFFECTIVE filter
  state into its probe — setting `f=1` alone tells `_filtered()` the form
  was submitted, which turns off the co-op and off-season defaults.

An empty list explains itself and links to the nearest window that isn't
empty. A browser test asserts each option renders exactly what it promises.

## The weekly self-check

`selfcheck.py` runs `healthcheck.py` on a LaunchAgent (Sundays 09:00) and
**notifies only on FAIL, never on a warning**. A notification that fires for
non-problems gets ignored, and then the one that matters gets ignored too.

The result is recorded in `app_state`, and the dashboard shows a banner when
the last check failed — so a notification that fired while you were away
from the machine is not lost.

## A health check must not destroy what it reports on

`healthcheck.py` and `schedule.sh status` both fetched `/` to prove the
dashboard was alive. Fetching `/` calls `register_visit()`, which is what
decides the NEW badges — so **running the health check silently cleared the
badges it was reporting on**, and nothing in its output would ever have
shown it.

Two defences, because one wasn't enough:

- `/healthz` — a liveness endpoint that reads and writes nothing. Probes
  use this (`config.DASHBOARD_HEALTH_URL`).
- A guard on `/` — a visit is registered only when the request's `Accept`
  header names `text/html` explicitly. Note this tests the RAW header
  rather than `request.accept_mimetypes.accept_html`, which returns True
  for the `*/*` that curl sends: wildcard matching makes every probe look
  like a browser.

`storage.current_visit_basis()` is the read-only counterpart to
`register_visit()`. It returns `""` rather than falling back to "now" —
falling back made two consecutive reads disagree.

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
4b. **Sources differ on whether they publish same-day rows.** The original
   five never did — the freshest age any reported was `1d`, because they
   are bot-generated on a lag. Chieler and DereC4 do, so postings at age 0
   now exist. A "posted today" filter was removed for being unmatchable and
   then restored when it stopped being so; the durable fix was not the
   removal but `config.AGE_WINDOWS` carrying a live count per option, so a
   window that is empty looks empty rather than broken.
5. **The legend documents 🛂 and 🇺🇸 markers, but no row uses them.** Those
   fields parse to False from this source; kept for future sources.

## Filter interaction worth remembering

The steep freshness curve puts every stale posting **below** the low-fit
threshold. So the dashboard's "show them" link must lift *both* the age cutoff
and the low-fit filter — lifting only the age filter appears to do nothing.

## Files that never get committed

`profile.md` (the résumé), `letters/`, `internships.db`, `logs/`, `.venv/`.
The ignore rules were verified before any personal data was written to disk.
