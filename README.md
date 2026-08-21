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
python3 -m venv .venv           # create an isolated Python environment
source .venv/bin/activate       # switch into it (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
```

The virtual environment keeps this project's packages separate from the rest of
the system, so installing something here can't break another project.

## Everyday use

```bash
python3 refresh.py    # fetch the latest listings, score them, show what's new
python3 app.py        # open the dashboard at http://127.0.0.1:5000
```

Run `refresh.py` whenever you want fresh data — the source updates daily. The
dashboard also has a **Refresh listings** button that does the same thing.

Run the tests any time with `python3 tests.py`.

---

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
storage.py      SQLite: postings, first_seen timestamps, applied marks
tests.py        checks the logic that's easy to get quietly wrong
sources/
  base.py             what every source must provide (the Posting shape)
  simplify_readme.py  fetching + parsing this particular repo
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
