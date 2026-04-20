"""
Phase C: pick 20-30 diagnostic samples from enriched/parsed PRs.

Criteria (agent consensus: diversity > volume):
  1. Only regressions (verdict == "degrade")
  2. Language diversity: aim ~40% Rust, ~35% Python, ~25% TypeScript
  3. Magnitude diversity: some small (<10%), some medium (10-25%), some large (>25%)
  4. Outcome diversity: some merged-anyway, some not merged, some with subsequent
     fixes (commits > 1)
  5. Must have base_sha + head_sha (else we can't reproduce locally)
  6. Skip bot comments with n_regressed == 0 and overall_pct == 0 (noise)
  7. Prefer PRs with clear single-benchmark regressions (per-function diagnostic
     value) over multi-benchmark cascades

Output: labeled/diagnostic_set.jsonl — 20-30 rows sorted by (language, magnitude).
Also labeled/summary.md — human-readable table of picks for manual review.
"""

from __future__ import annotations

import json
import pathlib
import random
from collections import defaultdict

PARSED_DIR = pathlib.Path("/home/dev/codspeed-atlas/parsed")
OUT_DIR = pathlib.Path("/home/dev/codspeed-atlas/labeled")

REPO_LANG = {
    "biomejs/biome": "Rust",
    "langchain-ai/langchain": "Python",
    "pydantic/pydantic": "Python",
    "vercel/next.js": "TypeScript",
    "pydantic/pydantic-core": "Rust",
    "astral-sh/ruff": "Rust",
    "withastro/astro": "TypeScript",
    "fastapi/fastapi": "Python",
    "astral-sh/uv": "Rust",
    "tursodatabase/turso": "Rust",
    "vercel/turborepo": "Rust",
}

LANG_TARGETS = {"Rust": 31, "Python": 22, "TypeScript": 12}  # totals 65 (ruff focal +5)
MAGNITUDE_BINS = [("small", 0, 10), ("medium", 10, 25), ("large", 25, 1000)]
TOTAL_TARGET = 65  # bumped from 60 to accommodate ruff focal expansion
REPO_FLOOR = 2          # every repo with regressions gets at least 2 picks
WALLTIME_FLOOR = 22     # WallTime mode picks must be >= 22
FOCAL_REPO = "astral-sh/ruff"     # within-repo mode comparison target
FOCAL_SIM_MIN = 5       # force ≥5 Simulation cases in focal repo
FOCAL_WT_MIN = 5        # force ≥5 WallTime cases in focal repo


def magnitude_bin(pct: float | None) -> str:
    if pct is None:
        return "unknown"
    p = abs(pct)
    for name, lo, hi in MAGNITUDE_BINS:
        if lo <= p < hi:
            return name
    return "unknown"


def outcome_class(record: dict) -> str:
    pr = record.get("pr_detail") or {}
    merged = pr.get("merged")
    commits = pr.get("commits") or 0
    state = record.get("state")
    if merged is True:
        if commits > 1:
            return "merged_with_fix"
        return "merged_as_is"
    if state == "closed" and not merged:
        return "abandoned"
    if state == "open":
        return "open"
    return "unknown"


def load_enriched() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(PARSED_DIR.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def get_mode(r: dict) -> str:
    changes = r.get("parsed", {}).get("changes") or []
    if changes and changes[0].get("mode"):
        return changes[0]["mode"]
    return "Unknown"


def diagnostic_score(r: dict) -> tuple:
    """Higher tuple = better diagnostic case. For sort descending."""
    p = r.get("parsed", {})
    overall = abs(p.get("overall_pct") or 0)

    # Outcome priority: merged_with_regression > abandoned > open
    outcome_pri = {
        "merged_as_is": 5,       # gold: team saw warning, merged anyway
        "merged_with_fix": 4,    # team tried to fix, committed multiple times
        "abandoned": 3,          # team gave up on PR
        "open": 2,
        "unknown": 1,
    }.get(r["_outcome"], 0)

    return (
        1 if r["_single_bench"] else 0,  # prefer single-bench clarity
        outcome_pri,
        overall,
    )


def pick(rows: list[dict], seed: int = 42) -> list[dict]:
    random.seed(seed)

    # Filter to valid regressions with reproducibility preconditions
    valid = []
    for r in rows:
        p = r.get("parsed", {})
        if p.get("verdict") != "degrade":
            continue
        if p.get("overall_pct") is None or p["overall_pct"] == 0:
            continue
        if not (p.get("base_sha") and p.get("head_sha")):
            continue
        if p.get("n_regressed") == 0:
            continue
        r["_lang"] = REPO_LANG.get(r["repo"], "Other")
        r["_magnitude_bin"] = magnitude_bin(p.get("overall_pct"))
        r["_outcome"] = outcome_class(r)
        r["_single_bench"] = p.get("n_regressed") == 1
        r["_mode"] = get_mode(r)
        valid.append(r)

    picks: list[dict] = []
    picked_ids: set = set()

    def pr_id(r: dict) -> tuple:
        return (r["repo"], r["number"])

    def add(r: dict) -> None:
        if pr_id(r) in picked_ids:
            return
        picks.append(r)
        picked_ids.add(pr_id(r))

    # ----- Phase 1: repo floor -----
    # Every repo with regressions gets top-N by diagnostic_score
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in valid:
        by_repo[r["repo"]].append(r)
    for repo, cands in by_repo.items():
        cands.sort(key=diagnostic_score, reverse=True)
        for r in cands[:REPO_FLOOR]:
            add(r)

    # ----- Phase 1.5: FOCAL REPO within-repo mode diversity -----
    # For the focal repo (ruff), force at least FOCAL_SIM_MIN Simulation cases
    # AND FOCAL_WT_MIN WallTime cases. This enables within-repo mode comparison
    # that the other 10 repos can't support (they're single-mode).
    focal_cands = by_repo.get(FOCAL_REPO, [])
    focal_sim = [r for r in focal_cands if r["_mode"] == "Simulation"]
    focal_wt = [r for r in focal_cands if r["_mode"] == "WallTime"]
    focal_sim.sort(key=diagnostic_score, reverse=True)
    focal_wt.sort(key=diagnostic_score, reverse=True)

    current_focal_sim = sum(1 for r in picks if r["repo"] == FOCAL_REPO and r["_mode"] == "Simulation")
    current_focal_wt = sum(1 for r in picks if r["repo"] == FOCAL_REPO and r["_mode"] == "WallTime")

    for r in focal_sim:
        if current_focal_sim >= FOCAL_SIM_MIN:
            break
        if pr_id(r) not in picked_ids:
            add(r)
            current_focal_sim += 1

    for r in focal_wt:
        if current_focal_wt >= FOCAL_WT_MIN:
            break
        if pr_id(r) not in picked_ids:
            add(r)
            current_focal_wt += 1

    # ----- Phase 2: WallTime floor -----
    # If WallTime picks < WALLTIME_FLOOR, add top WallTime candidates
    walltime_cnt = sum(1 for r in picks if r["_mode"] == "WallTime")
    if walltime_cnt < WALLTIME_FLOOR:
        wt_candidates = [
            r for r in valid
            if r["_mode"] == "WallTime" and pr_id(r) not in picked_ids
        ]
        wt_candidates.sort(key=diagnostic_score, reverse=True)
        need = WALLTIME_FLOOR - walltime_cnt
        for r in wt_candidates[:need]:
            add(r)

    # ----- Phase 3: fill to TOTAL_TARGET maintaining language balance -----
    # Count current language distribution
    def current_lang_count() -> dict[str, int]:
        c = defaultdict(int)
        for r in picks:
            c[r["_lang"]] += 1
        return c

    remaining_candidates = [r for r in valid if pr_id(r) not in picked_ids]
    remaining_candidates.sort(key=diagnostic_score, reverse=True)

    while len(picks) < TOTAL_TARGET and remaining_candidates:
        cur = current_lang_count()
        # Find the language most under-target (relative to LANG_TARGETS)
        most_needed = None
        max_deficit = -999
        for lang, target in LANG_TARGETS.items():
            deficit = target - cur.get(lang, 0)
            if deficit > max_deficit:
                max_deficit = deficit
                most_needed = lang

        # Take the best candidate of that language; if none, take any best
        pick_r = None
        for r in remaining_candidates:
            if r["_lang"] == most_needed:
                pick_r = r
                break
        if pick_r is None:
            pick_r = remaining_candidates[0]

        add(pick_r)
        remaining_candidates.remove(pick_r)

    # ----- Stable output sort -----
    picks.sort(
        key=lambda r: (
            r["_lang"],
            r["_magnitude_bin"],
            -(r.get("parsed", {}).get("overall_pct") or 0),
        )
    )
    return picks


def write_outputs(picks: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jsonl_path = OUT_DIR / "diagnostic_set.jsonl"
    with jsonl_path.open("w") as f:
        for r in picks:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    md_path = OUT_DIR / "summary.md"
    lines = [
        "# Diagnostic Set Summary",
        "",
        f"Total: **{len(picks)}** cases",
        "",
        "| # | Repo | Lang | PR | Overall % | Bench | Outcome | Merged? | Head SHA |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(picks, 1):
        p = r.get("parsed", {})
        pr = r.get("pr_detail") or {}
        first_change = (p.get("changes") or [{}])[0] if p.get("changes") else {}
        bench = first_change.get("benchmark") or "-"
        lines.append(
            f"| {i} | {r['repo']} | {r['_lang']} | "
            f"[#{r['number']}]({r['html_url']}) | "
            f"{p.get('overall_pct')} | `{bench}` | {r['_outcome']} | "
            f"{pr.get('merged')} | `{(p.get('head_sha') or '')[:7]}` |"
        )
    md_path.write_text("\n".join(lines) + "\n")

    print(f"Wrote {len(picks)} picks to {jsonl_path}")
    print(f"Wrote summary to {md_path}")


def main() -> None:
    rows = load_enriched()
    print(f"Loaded {len(rows)} enriched PRs")
    picks = pick(rows)
    by_lang: dict[str, int] = defaultdict(int)
    by_mag: dict[str, int] = defaultdict(int)
    by_outcome: dict[str, int] = defaultdict(int)
    for r in picks:
        by_lang[r["_lang"]] += 1
        by_mag[r["_magnitude_bin"]] += 1
        by_outcome[r["_outcome"]] += 1
    print(f"By language: {dict(by_lang)}")
    print(f"By magnitude: {dict(by_mag)}")
    print(f"By outcome: {dict(by_outcome)}")
    write_outputs(picks)


if __name__ == "__main__":
    main()
