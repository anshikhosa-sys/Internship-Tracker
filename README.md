# Personalized Internship Finder

Finds Summer 2027 internship postings and ranks them by how well they fit my
profile, so the best-fit roles are at the top instead of a wall of generic SWE
listings.

Runs entirely on my own machine. No hosting, no account, no API keys.

Data comes from [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships),
which is updated daily.

---

## Setup (once)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./scripts/schedule.sh install
```

That's it. Refreshes run at 07:30, 11:30 and 16:30, at login, and whenever
you open the dashboard with data more than a few hours old. The dashboard
stays live at **http://127.0.0.1:5000** — bookmark it.

**Is it really updating?** Yes, but not on the schedule above. launchd
replays a slot missed while the Mac was asleep — only one, however many were
missed — and never replays one missed while it was off. Real runs land at
06:40, 10:36, 15:38 against a 07:30/11:30/16:30 schedule. What actually
guarantees currency is the refresh when you **open the page**: it re-fetches
all five sources in under a second if the data has aged past three hours.
The header says `updated just now` / `updated 2 hours ago` so you never have
to wonder.

## Everyday use

Open the bookmark. The list is already filtered to roles worth applying to:
work down it, click **Apply**, set the stage dropdown. There is nothing to
configure and nothing to decide.

| Command | What it does |
|---|---|
| `python3 healthcheck.py` | Is everything actually working? |
| `python3 notify.py` | Test macOS notifications, with fix instructions |
| `python3 browser_tests.py` | Drive the real UI in a real browser |
| `python3 selfcheck.py` | Run every check and notify if anything failed |
| `python3 refresh.py` | Force a refresh now |
| `./scripts/schedule.sh status` | Are the background jobs alive? |
| `python3 tests.py` | Run the test suite |

## What's on the list, and what isn't

The dashboard is meant to be a **don't-think-just-apply list**. Three gates
run before anything appears:

1. **It must be classifiable.** A posting matching no role family is one the
   tool doesn't understand. Those were 56% of an earlier list and included
   textile engineering, geoscience and actuarial roles.
2. **You must be a plausible candidate.** Below `MIN_CANDIDACY` means
   something in the title works against you — an advanced degree, a
   seniority level, a technology your résumé doesn't support.
3. **It must be recent enough to still be open**, judged per company tier.

Deliberately excluded: quant and trading firms, data *analysis* (as opposed
to data *engineering*), ML *research* (as opposed to ML *infrastructure*),
co-ops, off-season terms, and non-technical product roles.

Tick **Show low-fit** to see everything anyway. Nothing is ever deleted.

### Who's hiring counts, not just the job title

The same title is a different job at a different employer. "Application
Engineering Intern" builds the product at a software company; at a
window-blinds manufacturer it's internal IT. So the employer multiplies
preference:

| Badge | Examples | Effect |
|---|---|---|
| **AI / frontier** | Anthropic, Palantir, NVIDIA, Databricks, SpaceX | ×1.20 |
| **Big tech** | Google, Microsoft, Meta, TikTok, Amazon | ×1.12 |
| **Tech** | Notion, Figma, Datadog, Replit, Rippling | ×1.05 |
| *(unrecognized)* | most small startups | ×0.90 |
| **Non-tech employer** | AbbVie, Devon Energy, Deloitte, Bank of America | ×0.40 |

A genuine ML-infrastructure or data-platform role escapes most of the
non-tech penalty — that work is real wherever it happens.

Tick **Tech employers only** to hide non-tech employers entirely. Promoting
a company is a one-line edit to `EMPLOYER_NAMES` in `config.py`.

### Companies that cap applications

Some employers only accept so many applications per cycle — TikTok and
ByteDance share a pool of **2**. `APPLICATION_LIMITS` in `config.py` makes
that a fact the tool knows rather than one you have to remember.

Once you mark an application, the list stops offering more roles there than
you have slots left, and every card shows `1 of 2 left`. When a quota is
spent, that company's remaining roles drop off the list entirely — they are
no longer things you can do. **show anyway** brings them back.

### One company can't take over the list

TikTok posts 191 roles. Ranked on score alone it took 7 of the top 20, which
turns a list of actions into a wall of one employer. The page shows your best
`MAX_PER_COMPANY` (3) at each company and says how many it held back, with a
**show all** link. Nothing is dropped from the database, and anything you've
applied to is never hidden by it.

## It checks itself

A LaunchAgent runs the full health check every Sunday at 09:00. It is
**silent when everything works** and notifies only when something actually
broke — never for warnings, because an alert that cries wolf gets ignored.
If you were away when it fired, the dashboard shows a banner until it's
fixed.

`healthcheck.py` includes `browser_tests.py`, which drives a real Chromium
through the real interface: it clicks the copy buttons and reads the
clipboard back, changes an application stage and reloads to confirm it
saved, types a note and reloads to confirm it persisted. It runs against a
**copy** of your database, so it can never touch your real applications.

Two copy-button bugs once shipped while every Python test passed — because
neither bug was in Python. That is what this exists to prevent.

## Your data is in a separate file

`applications.db` holds what you applied to. It is **not** in
`internships.db`, deliberately: the postings database is rebuildable in under
a second, and deleting it must never cost you an application record.
`applications.json` is a third, human-readable copy.

## Where the postings come from

Five lists, merged and deduplicated — about 970 rows collapsing to ~750
unique postings:

- SimplifyJobs/Summer2027-Internships
- vanshb03/Summer2027-Internships
- speedyapply/2027-SWE-College-Jobs
- speedyapply/2027-AI-College-Jobs
- sndsh404/summer-2027-internships

## Tuning the rankings

**Everything that affects ranking lives in [`config.py`](config.py).** No score
numbers or keywords exist anywhere else in the codebase.

| If you want to... | Edit this in `config.py` |
|---|---|
| Boost a kind of role | Add keywords to its entry in `ROLE_TIERS`, or raise its `points` |
| Change which roles rank highest | Change the `points` values in `ROLE_TIERS` |
| Care more about a topic | Raise its number in `FOCUS_BONUSES` |
| Push irrelevant roles down | Add keywords to `OUT_OF_SCOPE_KEYWORDS` |
| Include Quant / Hardware roles | Add them to `INGEST_CATEGORIES` |
| See advanced-degree roles ranked normally | Set `ADVANCED_DEGREE_PENALTY = 0` |
| Change the fit badges | Adjust `STRONG_FIT_THRESHOLD` / `GOOD_FIT_THRESHOLD` |
| Turn off notifications | Set `NOTIFY_ON_STRONG_FIT = False` |
| Get notified more or less often | Adjust `NOTIFY_THRESHOLD` |
| Change how long badges persist | Adjust `VISIT_SESSION_MINUTES` |

After editing, just run `python3 refresh.py` again. Scores are recomputed from
scratch every run, so changes take effect immediately and you never need to
delete the database.

### How a score is built

Each posting accumulates:

1. **Role tier** — the heaviest weight. Only the single best-matching tier
   counts, so a "Solutions Engineer" doesn't also collect generic SWE points.
2. **Category bonus** — being listed under Product Management is itself a signal.
3. **Focus bonuses** — AI, ML, infrastructure, data, systems, customer-facing.
   These stack, but are capped by `MAX_FOCUS_BONUS` so a keyword-stuffed title
   can't overturn the tiers.
4. **Out-of-scope fields** — quant finance, hardware, and other tracks this
   search isn't pointed at, so they sort to the bottom rather than crowding
   out the roles being looked for.

Every posting stores *why* it scored what it did. Click **"Why this score?"** on
any row in the dashboard to see the full breakdown.

---

## How the code is organized

```
config.py       ★ all keywords and weights — the only file you normally edit
refresh.py      the command: fetch → score → store → report what's new
app.py          the Flask dashboard
scorer.py       applies config.py's weights (contains no numbers itself)
storage.py      SQLite: postings, first_seen, applied marks, visit tracking
notify.py       macOS notifications for strong new matches
tests.py        checks the logic that's easy to get quietly wrong
sources/
  base.py             what every source must provide (the Posting shape)
  simplify_readme.py  fetching + parsing this particular repo
scripts/
  schedule.sh         install/remove the daily automatic refresh
templates/      the dashboard's HTML
static/         the dashboard's CSS
```

The layering is deliberate: `sources/` knows how to **get** postings, `scorer.py`
knows how to **rank** them, and neither knows the other exists. Both only deal
in `Posting` objects.

### Adding another source later

To add `SimplifyJobs/New-Grad-Positions` at graduation:

1. Add a file in `sources/` that returns `Posting` objects.
2. Add it to the `SOURCES` list in `refresh.py`.

That's the whole change. Scoring, storage, and the dashboard need no edits.
(That repo uses the same table format, so it may only need a different URL
passed to the existing `SimplifyReadmeSource`.)

---

## Notes on the data

Things learned by inspecting the source, which the parser handles:

- **The listings are HTML tables inside the README**, not markdown tables. The
  usual `line.split("|")` approach returns nothing.
- **Rows using `↳` as the company mean "same company as above."** There are
  ~230 of them; parsed literally you'd get hundreds of postings from a company
  called `↳`.
- **There's no posted-date column** — only a relative "Age" (`18d`, `1mo`). The
  displayed date is derived from that and is approximate. The **NEW** flag
  ignores it and uses our own `first_seen` timestamp, which is exact.
- **Closed roles are already excluded upstream** — they move to a separate
  `README-Inactive.md`.
- Postings that drop off the source are marked inactive rather than deleted, so
  a role you'd applied to never vanishes from your records.

## Files that aren't committed

`internships.db` is ignored by git. It holds your applied marks, which are
personal, and it's fully rebuildable from `refresh.py` anyway — except for the
applied marks, so **back it up if you've been tracking applications for a
while**:

```bash
cp internships.db internships.db.backup
```
