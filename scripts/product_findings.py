#!/usr/bin/env python3
"""
Compute 3 product-oriented findings from the 65-case diagnostic set.

Findings:
  F1: flag-to-outcome distribution (merged_as_is vs merged_with_fix vs abandoned vs open)
  F2: per-benchmark repeat-flag counts (which benchmarks are flagged most often)
  F3: comment-to-merge latency distribution (quartiles)

Runs on stdlib only. Output: JSON + human-readable summary.
"""
from __future__ import annotations
import json
import pathlib
from collections import Counter
from datetime import datetime

DIAG = pathlib.Path("/home/dev/codspeed-atlas/labeled/diagnostic_set.jsonl")


def load_cases() -> list[dict]:
    cases = []
    with DIAG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def iso_to_dt(s: str | None):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def finding_1_outcome_distribution(cases):
    """Count outcomes across 65 cases."""
    outcomes = Counter()
    by_repo = {}
    for c in cases:
        outcome = c.get("outcome") or c.get("parsed", {}).get("outcome") or "unknown"
        # If outcome not explicit, derive from pr_detail + state
        if outcome == "unknown":
            pr = c.get("pr_detail") or {}
            state = c.get("state")
            if pr.get("merged"):
                outcome = "merged"
            elif state == "closed":
                outcome = "abandoned"
            elif state == "open":
                outcome = "open"
        outcomes[outcome] += 1
        repo = c["repo"]
        by_repo.setdefault(repo, Counter())[outcome] += 1
    return outcomes, by_repo


def finding_2_benchmark_repeats(cases):
    """Which benchmarks are flagged most often across the 65 cases?"""
    bench_count = Counter()
    bench_by_repo = Counter()
    for c in cases:
        parsed = c.get("parsed", {})
        for ch in parsed.get("changes", []):
            bench = ch.get("benchmark") or "(unknown)"
            key = f"{c['repo']}::{bench}"
            bench_by_repo[key] += 1
            bench_count[bench] += 1
    return bench_count, bench_by_repo


def finding_3_response_latency(cases):
    """Distribution of (merged_at or closed_at) - comment_created_at in minutes."""
    latencies = []
    for c in cases:
        ct = iso_to_dt(c.get("comment_created_at"))
        pr = c.get("pr_detail") or {}
        ma = iso_to_dt(pr.get("merged_at"))
        ca = iso_to_dt(c.get("closed_at"))
        end = ma or ca
        if ct and end:
            delta_min = (end - ct).total_seconds() / 60.0
            if delta_min > 0:  # ignore negative (bot commented after merge race)
                latencies.append(delta_min)
    latencies.sort()
    n = len(latencies)

    def pct(p):
        if not latencies:
            return None
        k = int(p * (n - 1) / 100)
        return latencies[k]

    bucket = {
        "under_15min": sum(1 for x in latencies if x < 15),
        "15min_to_1day": sum(1 for x in latencies if 15 <= x < 60 * 24),
        "1_to_7_days": sum(1 for x in latencies if 60 * 24 <= x < 60 * 24 * 7),
        "over_7_days": sum(1 for x in latencies if x >= 60 * 24 * 7),
    }

    return {
        "n_with_latency": n,
        "p25_min": pct(25),
        "p50_min": pct(50),
        "p75_min": pct(75),
        "p90_min": pct(90),
        "buckets": bucket,
    }


def main():
    cases = load_cases()
    print(f"Loaded {len(cases)} cases from diagnostic_set.jsonl\n")

    # F1
    outcomes, by_repo = finding_1_outcome_distribution(cases)
    print("=" * 60)
    print("F1: Outcome distribution across 65 cases")
    print("=" * 60)
    total = sum(outcomes.values())
    for o, n in outcomes.most_common():
        print(f"  {o:<20} {n:>3}  ({100*n/total:>5.1f}%)")
    print()
    print("Outcome by repo (top 5 repos by case count):")
    sorted_repos = sorted(by_repo.items(), key=lambda x: -sum(x[1].values()))[:5]
    for repo, counter in sorted_repos:
        parts = [f"{k}={v}" for k, v in counter.most_common()]
        print(f"  {repo:<30} total={sum(counter.values())}  {' '.join(parts)}")
    print()

    # F2
    bench_count, bench_by_repo = finding_2_benchmark_repeats(cases)
    print("=" * 60)
    print("F2: Top flagged benchmarks across 65 cases")
    print("=" * 60)
    for bench, n in bench_count.most_common(10):
        print(f"  {n:>3}x  {bench}")
    print()
    print("Top repo::benchmark concentrations:")
    for key, n in bench_by_repo.most_common(5):
        print(f"  {n:>3}x  {key}")
    print()

    # F3
    lat = finding_3_response_latency(cases)
    print("=" * 60)
    print("F3: Comment-to-close latency distribution")
    print("=" * 60)
    print(f"  N with latency: {lat['n_with_latency']}")
    print(f"  p25:  {lat['p25_min']:>10.1f} min  ({(lat['p25_min'] or 0)/60:.1f} hours)")
    print(f"  p50:  {lat['p50_min']:>10.1f} min  ({(lat['p50_min'] or 0)/60:.1f} hours)")
    print(f"  p75:  {lat['p75_min']:>10.1f} min  ({(lat['p75_min'] or 0)/60:.1f} hours)")
    print(f"  p90:  {lat['p90_min']:>10.1f} min  ({(lat['p90_min'] or 0)/60/24:.1f} days)")
    print()
    print("  Bucket distribution:")
    for bname, n in lat["buckets"].items():
        print(f"    {bname:<20} {n:>3}")
    print()

    # write machine-readable output
    out = {
        "n_cases": len(cases),
        "f1_outcomes": dict(outcomes),
        "f1_by_repo": {k: dict(v) for k, v in by_repo.items()},
        "f2_top_benchmarks": bench_count.most_common(10),
        "f2_top_repo_bench": bench_by_repo.most_common(10),
        "f3_latency": lat,
    }
    outpath = pathlib.Path("/home/dev/codspeed-atlas/labeled/product_findings.json")
    outpath.write_text(json.dumps(out, indent=2))
    print(f"Written: {outpath}")


if __name__ == "__main__":
    main()
