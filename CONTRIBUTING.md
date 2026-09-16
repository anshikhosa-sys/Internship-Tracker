# Contributing

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py profile create --id example --resume examples/resume_example.md \
    --prefs examples/preferences_example.json
```

## Before opening a pull request

```bash
python3 tests.py            # unit and integration suite
python3 browser_tests.py    # required for any change to template JavaScript
python3 evaluate.py --profile example --fixture eval/fixtures/example_postings.jsonl --compare
```

The third one matters most: **a scoring change is not finished until it is
measured.** If a gated metric drops, either the change is wrong or the baseline
needs re-saving deliberately, with the reason in the commit message.

## The rules that shape this codebase

They are in [CLAUDE.md](CLAUDE.md) and they are short. The ones that come up most:

- **Config is data, logic is code.** No number, keyword, or company name
  belongs in `scoring/`, `profile/`, or `workflow/`; add it to `jobrank/config/`.
- **Nothing is tuned to one person.** Anything that only makes sense for one
  user is derived from their profile.
- **Missing data is neutral, not negative.**
- **Zero cost.** No paid API, SDK, or key; a test asserts it.
- **Whole-word matching, never substrings.** Use `jobrank.textmatch` — it
  handles `c++` and `.net`, which `\b` cannot.
- **A test that cannot run must fail, never pass quietly.**

## Adding a job source

Implement one class in `jobrank/sources/`, export it, and add it to the list in
`refresh.py`. Parsing tests run against fixture text, never the network.

## Commit messages

Say what changed and why, in prose. If a measurement motivated the change,
include the numbers.
