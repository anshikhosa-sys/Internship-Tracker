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
python3 refresh.py         # fetch the latest listings and show what's new
python3 app.py             # serve the dashboard at http://127.0.0.1:5000
python3 app.py --debug     # same, with auto-reload while editing code
```

Both are optional once the background jobs are installed — see below.

Run `refresh.py` whenever you want fresh data. The dashboard also has a
**Refresh listings** button that does the same thing.

Run the tests any time with `python3 tests.py`.

## Running it hands-off

```bash
./scripts/schedule.sh install     # set up both background jobs
./scripts/schedule.sh status      # what's running, what it last did
./scripts/schedule.sh run-now     # force a refresh immediately, to test
./scripts/schedule.sh restart     # reload after editing code
./scripts/schedule.sh uninstall   # remove both
```

That installs two macOS **LaunchAgents** in `~/Library/LaunchAgents/`:

| Job | What it does |
|---|---|
| `com.internship-finder.daily` | Runs `refresh.py` at 08:00 every day |
| `com.internship-finder.dashboard` | Keeps the dashboard alive at `127.0.0.1:5000` |

Together they mean there's nothing to type: listings update each morning, and
the dashboard is always there — bookmark it like any other site. The dashboard
job uses `KeepAlive`, so it restarts itself after a crash, a reboot, or a
logout.

No admin rights needed — LaunchAgents live in your home folder and run as you.

**Why launchd rather than cron:** if the Mac is asleep at the scheduled time,
cron skips that day silently and you'd get no update. launchd notices the
missed run and fires it when the machine next wakes — which matters for a
morning schedule on a laptop that's closed overnight.

To change the time, edit `RUN_HOUR` / `RUN_MINUTE` at the top of
`scripts/schedule.sh` and re-run `install`. Output goes to `logs/refresh.log`.

## Cover letters and work experience

Every posting has an **Application prep** button with two copy-paste prompts:

- **Cover letter** — a draft, reusable talking points, an honest gap list, and
  a frank verdict on whether the role is worth your time.
- **Work experience** — your experience rewritten for that role's audience,
  in a short version for character-limited fields and a full one.

**Both are free and always will be.** They're text assembled on your machine.
Copy one into claude.ai, ChatGPT, or anything else, and paste the result back.
There is no API client installed and no key to configure.

The prompts adapt to the role family: an FDE reviewer wants evidence of
customer-facing work, a SWE reviewer wants depth on the hardest system you've
shipped. Same résumé, different pitch — see `ROLE_FAMILY_GUIDANCE` in
`config.py`.

**Paste the real job description** into the box on that page. The source only
gives us a job title, so this is the single biggest quality lever available —
it also makes the gap list real, telling you what a role wants that you don't
have before an interview does.

They draft; they never submit. Application portals prohibit automated
submission and an application can't be unsent, so the irreversible step stays
yours.

Letters are written from `profile.md` (gitignored — copy `profile_example.md`
to start). The prompt restricts every claim to what that file states. **The
"Notes for the letter writer" section at the bottom of `profile.md` is the
highest-leverage thing to keep adding to** — it's where context lives that a
one-page résumé can't hold.

### Notifications

When a scheduled run finds new postings scoring at or above
`NOTIFY_THRESHOLD` (default 60), macOS shows **one** notification summarizing
them — not one per posting. Set `NOTIFY_ON_STRONG_FIT = False` for silent runs.
The dashboard's Refresh button never notifies, since you're already looking at
the results.

`NOTIFY_THRESHOLD` is deliberately separate from `STRONG_FIT_THRESHOLD`. The
badge threshold answers "what deserves a green highlight"; the notify
threshold answers "what deserves interrupting me". Measured against real data,
about 55 postings arrive daily, ~7 score 60+, and almost none score 100+ — so
tying notifications to the badge threshold left it silent for days while
relevant roles went by unannounced.

### What "NEW" means

**NEW = arrived since you last opened the dashboard**, not since the last
refresh. Those are different questions once refreshes are automatic: "since
the last run" would mean "in the last 24 hours", so skipping a few days would
silently stop flagging everything older than yesterday.

Page loads within `VISIT_SESSION_MINUTES` (default 30) count as the same
visit, so badges don't vanish while you're browsing. Coming back later starts
a new visit. **Mark all as seen** clears them on demand.

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
