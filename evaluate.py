"""
Measure the ranking against a golden set, and gate regressions.

    python3 evaluate.py --profile me                    metrics for the current config
    python3 evaluate.py --profile me --save-baseline    record them as the bar to beat
    python3 evaluate.py --profile me --compare          exit 1 if a gated metric regressed
    python3 evaluate.py --profile me --ablation         what each factor contributes
    python3 evaluate.py --profile example --fixture eval/fixtures/example_postings.jsonl

A scoring change is not done until --compare passes, or the baseline is
re-saved deliberately with the reason in the commit message.
"""

import argparse
import json
import sys

from jobrank.eval import harness


def _table(result: dict) -> None:
    m = result["metrics"]
    print(f"\nProfile {result['profile']}: {result['labels_usable']} usable labels "
          f"({result['labels_relevant']} relevant) over a pool of {result['pool_size']} postings")
    if result["labels_usable"] < result["labels_total"]:
        print(f"  ({result['labels_total'] - result['labels_usable']} labels point at postings not in the pool)")
    print(f"  factors off: {', '.join(result['disabled_factors']) or 'none'} · "
          f"semantic model: {result['semantic_model'] or 'none'} · config {result['config_fingerprint']}")
    r = result["random_baseline"]
    print(f"\n  {'':>4}  {'ranking':>9}  {'random':>7}")
    print(f"  {'AUC':>4}  {m['auc'] or 0:>9.3f}  {r['auc']:>7.3f}")
    print(f"  {'MRR':>4}  {m['mrr']:>9.3f}  {r['mrr']:>7.3f}")
    print(f"  mean rank of relevant: {m['mean_rank_relevant'] or float('nan'):.0f} (random {r['mean_rank_relevant']:.0f})")
    print(f"\n  {'k':>4}  {'precision':>9}  {'recall':>7}  {'nDCG':>6}   {'random P':>8}  {'random R':>8}  {'random nDCG':>11}")
    for k in sorted({int(key.split('@')[1]) for key in m if '@' in key}):
        print(f"  {k:>4}  {m[f'precision@{k}']:>9.3f}  {m[f'recall@{k}']:>7.3f}  {m[f'ndcg@{k}']:>6.3f}   "
              f"{r[f'precision@{k}']:>8.3f}  {r[f'recall@{k}']:>8.3f}  {r[f'ndcg@{k}']:>11.3f}")
    if result["worst_misses"]:
        print("\n  Relevant postings ranked lowest:")
        for miss in result["worst_misses"]:
            print(f"    #{miss['rank']:<5} score {miss['score']:>3}  {miss['company'][:22]:22s} {miss['role'][:48]}")
    print("\n  Precision counts unlabeled postings as not relevant, so it is a lower bound.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--fixture", help="JSONL postings to rank instead of the database")
    parser.add_argument("--with-freshness", action="store_true", help="include today's freshness in the ranking")
    parser.add_argument("--no-semantic", action="store_true", help="skip the embedding layer")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--compare", action="store_true", help="exit 1 if a gated metric regressed")
    parser.add_argument("--tolerance", type=float, default=harness.DEFAULT_TOLERANCE)
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    kwargs = dict(fixture=args.fixture, with_freshness=args.with_freshness, semantic=not args.no_semantic)
    try:
        if args.ablation:
            rows = harness.ablation(args.profile, **kwargs)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                print("\nAblation (AUC with each factor removed; positive delta = the factor helps):")
                for row in rows:
                    print(f"  {row['factor']:<14} {row['auc']:.3f}  {row['delta']:+.3f}")
            return 0
        result = harness.evaluate(args.profile, **kwargs)
    except (ValueError, LookupError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.json and not args.compare:
        print(json.dumps(result, indent=2))
    elif not args.json:
        _table(result)

    if args.save_baseline:
        print(f"\n  Baseline saved: {harness.save_baseline(result)}")

    if args.compare:
        baseline = harness.load_baseline(args.profile)
        if baseline is None:
            print(f"\nNo baseline for {args.profile}. Create one with --save-baseline.", file=sys.stderr)
            return 2
        report = harness.compare(result, baseline, args.tolerance)
        if args.json:
            print(json.dumps({"result": result, "comparison": report}, indent=2))
            return 1 if report["regressions"] else 0
        print(f"\n  Against baseline (tolerance {args.tolerance}):")
        for row in report["rows"]:
            flag = "  REGRESSED" if row["regressed"] else ""
            print(f"    {row['metric']:<20} {row['baseline']:.3f} -> {row['current']:.3f}  ({row['delta']:+.3f}){flag}")
        if not report["comparable"]:
            print("  ! Label set or pool changed since the baseline; deltas mix ranking and data changes.")
        if report["regressions"]:
            print(f"\nFAIL: {', '.join(report['regressions'])} regressed beyond tolerance.")
            return 1
        print("\nPASS: no gated metric regressed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
