"""
Preference validation and the interactive intake used by the CLI.

The web form and the CLI both funnel through `validate()`, so a preference the
form accepts is exactly one the CLI accepts. Errors are collected and returned
together rather than failing on the first, because a form that reports one
problem per submit is miserable to use.
"""

from __future__ import annotations

from typing import Any, Callable

from jobrank.config import profile as cfg
from jobrank.config import taxonomy
from jobrank.models import Preferences
from jobrank.resume import dates
from jobrank.roles import classify_title


class PreferenceError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.replace("\n", ",").split(",")
    out, seen = [], set()
    for item in value:
        item = str(item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item[: cfg.MAX_ITEM_CHARS])
    return out[: cfg.MAX_LIST_ITEMS]


def validate(data: dict[str, Any]) -> Preferences:
    problems: list[str] = []

    target_roles = _as_list(data.get("target_roles"))
    unknown = [r for r in target_roles if not classify_title(r)]
    if unknown:
        problems.append(f"Unrecognized target roles: {', '.join(unknown)}. Use a job title such as "
                        "'Software Engineer' or 'Data Engineer'.")

    seniority = [s.lower() for s in _as_list(data.get("seniority"))]
    bad = [s for s in seniority if s not in taxonomy.SENIORITY_LEVELS]
    if bad:
        problems.append(f"Unknown seniority {bad}; choose from {taxonomy.SENIORITY_LEVELS}.")

    remote = (data.get("remote") or "any").strip().lower()
    if remote not in cfg.REMOTE_CHOICES:
        problems.append(f"remote must be one of {cfg.REMOTE_CHOICES}.")

    sizes = [s.lower() for s in _as_list(data.get("company_sizes"))]
    bad = [s for s in sizes if s not in cfg.COMPANY_SIZE_CHOICES]
    if bad:
        problems.append(f"Unknown company sizes {bad}; choose from {cfg.COMPANY_SIZE_CHOICES}.")

    industries = [s.lower().replace(" ", "_") for s in _as_list(data.get("exclude_industries"))]
    bad = [s for s in industries if s not in taxonomy.INDUSTRIES]
    if bad:
        problems.append(f"Unknown industries {bad}; choose from {taxonomy.INDUSTRIES}.")

    earliest = data.get("earliest_start") or None
    if earliest:
        parsed = dates.parse_point(str(earliest))
        if not parsed:
            problems.append("earliest_start must look like 2027-05 or 'May 2027'.")
        earliest = parsed

    priorities: dict[str, int] = {}
    for factor, raw in (data.get("priorities") or {}).items():
        if factor not in cfg.PRIORITY_FACTORS:
            problems.append(f"Unknown priority '{factor}'; choose from {cfg.PRIORITY_FACTORS}.")
            continue
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            problems.append(f"Priority for {factor} must be a whole number 1-5.")
            continue
        if value not in cfg.PRIORITY_EXPONENTS:
            problems.append(f"Priority for {factor} must be 1-5.")
            continue
        priorities[factor] = value

    if problems:
        raise PreferenceError(problems)
    return Preferences(
        target_roles=target_roles, seniority=seniority,
        locations=_as_list(data.get("locations")), remote=remote, company_sizes=sizes,
        exclude_industries=industries, earliest_start=earliest, priorities=priorities,
    )


_QUESTIONS = [
    ("target_roles", "Target roles (comma-separated job titles)"),
    ("seniority", f"Seniority levels you want ({', '.join(taxonomy.SENIORITY_LEVELS)})"),
    ("locations", "Preferred locations (comma-separated; blank for anywhere)"),
    ("remote", f"Remote preference ({', '.join(cfg.REMOTE_CHOICES)})"),
    ("company_sizes", f"Company sizes ({', '.join(cfg.COMPANY_SIZE_CHOICES)}; blank for any)"),
    ("exclude_industries", "Industries to exclude (comma-separated; blank for none)"),
    ("earliest_start", "Earliest start date (YYYY-MM; blank for any)"),
]


def prompt(existing: Preferences | None = None, ask: Callable[[str], str] = input,
           say: Callable[[str], None] = print) -> Preferences:
    """Ask each question, re-asking only the ones that fail validation."""
    current = (existing or Preferences()).to_dict()
    answers: dict[str, Any] = dict(current)
    for key, question in _QUESTIONS:
        default = current.get(key)
        shown = ", ".join(default) if isinstance(default, list) else (default or "")
        reply = ask(f"{question} [{shown}]: ").strip()
        if reply:
            answers[key] = reply
    say(f"Rate how much each factor should matter, 1 (barely) to 5 (decisive). Blank keeps {cfg.DEFAULT_PRIORITY}.")
    priorities = dict(current.get("priorities") or {})
    for factor in cfg.PRIORITY_FACTORS:
        reply = ask(f"  {factor} [{priorities.get(factor, cfg.DEFAULT_PRIORITY)}]: ").strip()
        if reply:
            priorities[factor] = reply
    answers["priorities"] = priorities

    while True:
        try:
            return validate(answers)
        except PreferenceError as exc:
            for problem in exc.problems:
                say(f"  ! {problem}")
            for key, question in _QUESTIONS:
                if any(key.replace("_", " ") in p.lower() or key in p for p in exc.problems):
                    answers[key] = ask(f"{question}: ").strip()
            if any("priority" in p.lower() for p in exc.problems):
                answers["priorities"] = {f: ask(f"  {f} (1-5): ").strip() for f in cfg.PRIORITY_FACTORS}
