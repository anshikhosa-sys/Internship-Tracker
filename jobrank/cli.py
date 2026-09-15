"""
Command-line interface: `python3 run.py ...`.

    run.py profile create --id alex --resume resume.pdf [--prefs prefs.json]
    run.py profile show --id alex
    run.py profile list
    run.py prefs --id alex [--file prefs.json]
    run.py label seed|add|remove|list|queue --profile alex

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
# label (golden set)
# ---------------------------------------------------------------------------

def _posting_lookup() -> dict[str, dict]:
    from jobrank import storage

    conn = storage.connect()
    try:
        return {row["id"]: row for row in storage.load_postings(conn, include_inactive=True)}
    finally:
        conn.close()


def cmd_label_seed(args: argparse.Namespace) -> int:
    from jobrank import storage
    from jobrank.eval import golden

    conn = storage.connect()
    try:
        applications = [{"id": a["posting_id"], "company": a["company"] or "", "role": a["role"] or "",
                         "status": a["status"]} for a in storage.pipeline(conn)]
    finally:
        conn.close()
    labels = golden.load(args.profile)
    changed = golden.seed_from_applications(labels, applications)
    path = golden.save(args.profile, labels)
    print(f"Seeded {changed} labels from {len(applications)} applications -> {path} ({len(labels)} total).")
    return 0


def cmd_label_add(args: argparse.Namespace) -> int:
    from jobrank.eval import golden

    posting = _posting_lookup().get(args.posting)
    if posting is None:
        print(f"No posting {args.posting}.", file=sys.stderr)
        return 2
    labels = golden.load(args.profile)
    golden.upsert(labels, posting, args.relevance, note=args.note or "")
    golden.save(args.profile, labels)
    print(f"{args.relevance}  {posting['company']} — {posting['role']}")
    return 0


def cmd_label_remove(args: argparse.Namespace) -> int:
    from jobrank.eval import golden

    labels = golden.load(args.profile)
    if labels.pop(args.posting, None) is None:
        print(f"No label for {args.posting}.", file=sys.stderr)
        return 2
    golden.save(args.profile, labels)
    return 0


def cmd_label_list(args: argparse.Namespace) -> int:
    from jobrank.eval import golden

    labels = golden.load(args.profile)
    for label in sorted(labels.values(), key=lambda l: (-l.relevance, l.company.lower())):
        print(f"{label.relevance}  {label.posting_id}  {label.company[:24]:24s} {label.role[:50]:50s} {label.source}")
    print(f"\n{len(labels)} labels")
    return 0


def cmd_label_queue(args: argparse.Namespace, ask=input) -> int:
    """
    Label the CURRENT ranking's top unlabeled postings. These are exactly the
    items precision@k is computed over, and labeling them is what turns
    precision from a lower bound into a measurement.
    """
    from jobrank import ranking
    from jobrank.eval import golden

    ranked = ranking.rank(args.profile)
    if ranked is None:
        print(f"No profile {args.profile}.", file=sys.stderr)
        return 2
    labels = golden.load(args.profile)
    rows = {r["id"]: r for r in ranked.rows}
    queue = [pid for pid in ranked.ordered_ids if pid not in labels][: args.n]
    print("Relevance: 0 not relevant, 1 marginal, 2 relevant, 3 highly relevant. s = skip, q = quit.")
    done = 0
    for pid in queue:
        row = rows[pid]
        reply = ask(f"[{row['fit_score']:>3}] {row['company']} — {row['role']} ({row['location']}): ").strip().lower()
        if reply == "q":
            break
        if reply in {"0", "1", "2", "3"}:
            golden.upsert(labels, row, int(reply))
            done += 1
    golden.save(args.profile, labels)
    print(f"Labeled {done}; {len(labels)} total.")
    return 0


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

    label = sub.add_parser("label", help="manage golden relevance labels for evaluation")
    lsub = label.add_subparsers(dest="action", required=True)
    seed = lsub.add_parser("seed", help="label every application as relevant (stage-graded)")
    seed.add_argument("--profile", required=True)
    seed.set_defaults(func=cmd_label_seed)
    add = lsub.add_parser("add", help="label one posting 0-3")
    add.add_argument("--profile", required=True)
    add.add_argument("--posting", required=True)
    add.add_argument("--relevance", type=int, choices=range(4), required=True)
    add.add_argument("--note")
    add.set_defaults(func=cmd_label_add)
    remove = lsub.add_parser("remove", help="delete one label")
    remove.add_argument("--profile", required=True)
    remove.add_argument("--posting", required=True)
    remove.set_defaults(func=cmd_label_remove)
    lst = lsub.add_parser("list", help="print labels")
    lst.add_argument("--profile", required=True)
    lst.set_defaults(func=cmd_label_list)
    queue = lsub.add_parser("queue", help="interactively label the top unlabeled postings")
    queue.add_argument("--profile", required=True)
    queue.add_argument("-n", type=int, default=25)
    queue.set_defaults(func=cmd_label_queue)

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
