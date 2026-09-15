"""
Command-line interface: `python3 run.py ...`.

    run.py profile create --id alex --resume resume.pdf [--prefs prefs.json]
    run.py profile show --id alex
    run.py profile list
    run.py prefs --id alex [--file prefs.json]

Later phases register more commands here (refresh, explain, apply, label,
stats). Each command is a function taking parsed args and returning an exit
code; `main` only dispatches.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from jobrank.models import Preferences
from jobrank.profile import preferences as prefs_mod
from jobrank.profile import store
from jobrank.resume import parser as resume_parser

Command = Callable[[argparse.Namespace], int]
_registry: list[Callable[[argparse._SubParsersAction], None]] = []


def register(builder: Callable[[argparse._SubParsersAction], None]) -> Callable:
    _registry.append(builder)
    return builder


def _load_prefs_file(path: str) -> Preferences:
    with open(path, encoding="utf-8") as handle:
        return prefs_mod.validate(json.load(handle))


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def cmd_profile_create(args: argparse.Namespace) -> int:
    try:
        store.validate_user_id(args.id)
        resume = resume_parser.parse_file(args.resume)
        if args.prefs:
            preferences = _load_prefs_file(args.prefs)
        elif sys.stdin.isatty():
            print("No --prefs file given; answer a few questions (Enter keeps the default).")
            preferences = prefs_mod.prompt()
        else:
            preferences = Preferences()
    except (resume_parser.ResumeReadError, resume_parser.ResumeParseError) as exc:
        print(f"Could not build a profile: {exc}", file=sys.stderr)
        return 2
    except prefs_mod.PreferenceError as exc:
        print("Preferences are invalid:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"Could not build a profile: {exc}", file=sys.stderr)
        return 2

    profile = store.save(args.id, resume, preferences)
    print(f"Saved {store.path_for(args.id)} (extracted by {resume.extractor}).")
    _print_profile(profile, resume.warnings)
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    try:
        profile = store.load(args.id)
        resume, _ = store.load_inputs(args.id)
    except (store.ProfileNotFound, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(profile.to_dict(), indent=2))
    else:
        _print_profile(profile, resume.warnings)
    return 0


def cmd_profile_list(args: argparse.Namespace) -> int:
    ids = store.list_ids()
    print("\n".join(ids) if ids else "No profiles yet. Create one with: run.py profile create --id <id> --resume <file>")
    return 0


def cmd_prefs(args: argparse.Namespace) -> int:
    try:
        _, existing = store.load_inputs(args.id)
        preferences = _load_prefs_file(args.file) if args.file else prefs_mod.prompt(existing)
    except (store.ProfileNotFound, ValueError, OSError) as exc:
        problems = getattr(exc, "problems", None)
        print("\n".join(f"  - {p}" for p in problems) if problems else exc, file=sys.stderr)
        return 2
    profile = store.update_preferences(args.id, preferences)
    print(f"Updated preferences for {args.id}.")
    _print_profile(profile, [])
    return 0


def _print_profile(profile, warnings: list[str]) -> None:
    s = profile.seniority
    print(f"\nProfile: {profile.user_id}")
    print(f"  Seniority     {s.level} ({s.years_experience} credited years; "
          f"{'student, graduating ' + s.graduation if s.is_student and s.graduation else 'not a student'}; "
          f"eligible: {', '.join(s.eligible_levels)})")
    top_skills = sorted(profile.skills.items(), key=lambda kv: -kv[1])[:12]
    print(f"  Skills ({len(profile.skills)})   " + ", ".join(f"{k} {v:.2f}" for k, v in top_skills))
    top_roles = sorted(profile.role_affinity.items(), key=lambda kv: -kv[1])[:6]
    print("  Role affinity " + ", ".join(f"{k} {v:.2f}" for k, v in top_roles))
    print("  Factor weights " + ", ".join(f"{k}^{v}" for k, v in profile.factor_weights.items()))
    unmatched = profile.evidence.get("role_affinity", {}).get("_unmatched_targets")
    for note in list(warnings) + ([f"Target roles not recognized: {', '.join(unmatched)}"] if unmatched else []):
        print(f"  ! {note}")


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="jobrank: rank job postings for a profile.")
    sub = parser.add_subparsers(dest="command")

    profile = sub.add_parser("profile", help="create, show, or list profiles")
    psub = profile.add_subparsers(dest="action", required=True)
    create = psub.add_parser("create", help="parse a résumé and save a profile")
    create.add_argument("--id", required=True)
    create.add_argument("--resume", required=True, help="PDF, DOCX, TXT or Markdown")
    create.add_argument("--prefs", help="JSON preferences file (otherwise asked interactively)")
    create.set_defaults(func=cmd_profile_create)
    show = psub.add_parser("show", help="print a derived profile")
    show.add_argument("--id", required=True)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_profile_show)
    psub.add_parser("list", help="list profile ids").set_defaults(func=cmd_profile_list)

    prefs = sub.add_parser("prefs", help="update a profile's preferences")
    prefs.add_argument("--id", required=True)
    prefs.add_argument("--file", help="JSON preferences file (otherwise asked interactively)")
    prefs.set_defaults(func=cmd_prefs)

    for builder in _registry:
        builder(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)
