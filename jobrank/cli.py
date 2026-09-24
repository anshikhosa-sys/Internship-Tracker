"""
Command-line interface: `python3 run.py ...`.

    run.py profile create --id alex --resume resume.pdf
    run.py profile show --id alex
    run.py profile list
    run.py label seed|add|remove|list|queue --profile alex
    run.py --explain <posting_id> [--profile alex]
    run.py --stats

Each command is a function taking parsed args and returning an exit code;
`main` only dispatches.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from jobrank.profile import store
from jobrank.resume import parser as resume_parser

Command = Callable[[argparse.Namespace], int]
_registry: list[Callable[[argparse._SubParsersAction], None]] = []


def register(builder: Callable[[argparse._SubParsersAction], None]) -> Callable:
    _registry.append(builder)
    return builder


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def cmd_profile_create(args: argparse.Namespace) -> int:
    """A résumé is the whole profile. Nothing is asked about what you want."""
    try:
        store.validate_user_id(args.id)
        resume = resume_parser.parse_file(args.resume)
    except (resume_parser.ResumeReadError, resume_parser.ResumeParseError) as exc:
        print(f"Could not build a profile: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"Could not build a profile: {exc}", file=sys.stderr)
        return 2

    profile = store.save(args.id, resume)
    print(f"Saved {store.path_for(args.id)} (extracted by {resume.extractor}).")
    _print_profile(profile, resume.warnings)
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    try:
        profile = store.load(args.id)
        resume = store.load_resume(args.id)
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
# observability
# ---------------------------------------------------------------------------

def find_posting_id(query: str, ids) -> str | None:
    """An exact id, or a unique prefix of one (with or without the "job:" part)."""
    ids = list(ids)
    if query in ids:
        return query
    wanted = query if query.startswith("job:") else f"job:{query}"
    matches = [i for i in ids if i.startswith(wanted)]
    return matches[0] if len(matches) == 1 else None


def cmd_explain(args: argparse.Namespace) -> int:
    from jobrank import ranking
    from jobrank.scoring.explain import explain

    ranked = ranking.rank(args.profile, trigger="explain")
    if ranked is None:
        print("No profile to explain against.", file=sys.stderr)
        return 2
    if args.profile and ranked.profile.user_id != args.profile:
        print(f"No profile '{args.profile}'.", file=sys.stderr)
        return 2
    posting_id = find_posting_id(args.explain, ranked.results)
    if posting_id is None:
        print(f"No single active posting matches '{args.explain}'.", file=sys.stderr)
        return 2
    row = next(r for r in ranked.rows if r["id"] == posting_id)
    rank = ranked.ordered_ids.index(posting_id) + 1
    if args.json:
        print(json.dumps({"rank": rank, "total": len(ranked.ordered_ids), **ranked.results[posting_id].to_dict()},
                         indent=2))
    else:
        print(explain(ranked.profile, row, ranked.results[posting_id], rank, len(ranked.ordered_ids)))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from jobrank.ops import stats

    data = stats.collect()
    print(json.dumps(data, indent=2, default=str) if args.json else stats.format_text(data))
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="jobrank: rank job postings for a profile.")
    parser.add_argument("--explain", metavar="POSTING_ID", help="explain one posting's score, factor by factor")
    parser.add_argument("--profile", help="profile id for --explain (default: the active profile)")
    parser.add_argument("--stats", action="store_true", help="LLM usage, cache hit rates, coverage, recent runs")
    parser.add_argument("--json", action="store_true", help="machine-readable output for --explain/--stats")
    sub = parser.add_subparsers(dest="command")

    profile = sub.add_parser("profile", help="create, show, or list profiles")
    psub = profile.add_subparsers(dest="action", required=True)
    create = psub.add_parser("create", help="parse a résumé and save a profile")
    create.add_argument("--id", required=True)
    create.add_argument("--resume", required=True, help="PDF, DOCX, TXT or Markdown")
    create.set_defaults(func=cmd_profile_create)
    show = psub.add_parser("show", help="print a derived profile")
    show.add_argument("--id", required=True)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_profile_show)
    psub.add_parser("list", help="list profile ids").set_defaults(func=cmd_profile_list)

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

    describe = sub.add_parser("describe", help="fetch real job descriptions from job boards")
    describe.add_argument("-n", type=int, default=None,
                          help="stop after this many postings (default: every reachable one)")
    describe.set_defaults(func=cmd_describe)

    market = sub.add_parser("market", help="what the current corpus demands, and how you match it")
    market.add_argument("--profile", default=None, help="score a profile against the market")
    market.set_defaults(func=cmd_market)

    for builder in _registry:
        builder(sub)
    return parser


def cmd_describe(args) -> int:
    """
    Sources give a title and a link; the description lives on the job board.
    Without it the skills factor is matching a résumé against seven words.
    """
    from jobrank import refresh, storage

    conn = storage.connect()
    before = len(storage.postings_without_description(conn))
    saved = refresh.fetch_descriptions(conn, limit=args.n)
    after = len(storage.postings_without_description(conn))
    total = len(storage.load_postings(conn))
    print(f"Fetched {saved} descriptions ({before} missing before, {after} after).")
    print(f"Coverage: {total - after}/{total} active postings = {100 * (total - after) / max(1, total):.1f}%")
    return 0


def cmd_market(args) -> int:
    """What the live corpus asks for — the other half of the scoring model."""
    from jobrank import market as market_model
    from jobrank import storage
    from jobrank.config import taxonomy

    conn = storage.connect()
    rows = storage.load_postings(conn)
    model = market_model.build(rows)
    print(f"{model.total_postings} active postings, {model.described_postings} with a real description "
          f"({100 * model.described_postings / max(1, model.total_postings):.0f}%)\n")

    print("Most in demand (share of postings naming it):")
    ranked = sorted(model.skill_postings.items(), key=lambda kv: -kv[1])[:12]
    for skill, count in ranked:
        print(f"  {skill:<22} {count:5d}  {100 * count / model.total_postings:5.1f}%   rarity x{model.idf(skill):.2f}")

    if args.profile:
        from jobrank.profile import store
        profile = store.load(args.profile)
        if profile is None:
            print(f"\nNo profile '{args.profile}'.")
            return 2
        affinity = model.affinity(profile.skills)
        print(f"\nHow {args.profile}'s résumé covers what each role family demands:")
        for family, value in sorted(affinity.items(), key=lambda kv: -kv[1])[:10]:
            label = taxonomy.ROLE_FAMILIES[family]["label"]
            evidence = profile.role_affinity.get(family, 0.0)
            print(f"  {label:<34} market {value:.0%}   résumé evidence {evidence:.2f}")
        gaps = sorted(
            ((s, sh) for f in affinity for s, sh in model.demand(f).items()
             if profile.skills.get(s, 0) == 0 and model.idf(s) > 1.0),
            key=lambda kv: -kv[1])
        seen, top = set(), []
        for skill, share in gaps:
            if skill not in seen:
                seen.add(skill); top.append(skill)
        if top:
            print("\nMost valuable skills you do not have yet: " + ", ".join(top[:8]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.explain:
        return cmd_explain(args)
    if args.stats:
        return cmd_stats(args)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)
