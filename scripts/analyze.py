"""
W v2: per-case statistical analysis of the 60-case Atlas.

Per C1 agent review requirements:
  1. Unit of analysis = per-case (NOT per-repo dominant mode)
  2. Suppress per-repo rates for n<8 (show raw counts only)
  3. Split by date around 2026-03-16 (codspeed-optimize launch)
  4. Headline test = repo-stratified permutation test of merge-after-flag rate
     Sim vs WT + Wilson and bootstrap 95% CIs
  5. All terminology: "merge-after-flag" not "merge-through-warning"

No causal claims. Descriptive + one headline test + honest CIs.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
from collections import defaultdict, Counter
from datetime import datetime, timezone

ATLAS_PATH = pathlib.Path("/home/dev/codspeed-atlas/labeled/diagnostic_set.jsonl")
OUT_DIR = pathlib.Path("/home/dev/codspeed-atlas/labeled")

LAUNCH_DATE = datetime(2026, 3, 16, tzinfo=timezone.utc)
PER_REPO_REPORT_FLOOR = 8
BOOTSTRAP_REPS = 10_000
PERM_REPS = 10_000
SEED = 42


# ---------- helpers ----------

def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_rate_diff_ci(
    sim_successes: list[int],
    sim_trials: list[int],
    wt_successes: list[int],
    wt_trials: list[int],
    reps: int = BOOTSTRAP_REPS,
    seed: int = SEED,
) -> tuple[float, float, float]:
    """
    Repo-stratified bootstrap: resample WITHIN each repo-mode cell, recompute
    overall rate difference. Returns (point, lo95, hi95).
    """
    rng = random.Random(seed)
    def overall_rate(succ: list[int], trials: list[int]) -> float:
        t = sum(trials)
        return sum(succ) / t if t > 0 else 0.0

    obs_diff = overall_rate(sim_successes, sim_trials) - overall_rate(wt_successes, wt_trials)

    draws = []
    for _ in range(reps):
        # resample within sim strata
        sim_s = []
        sim_t = []
        for s, t in zip(sim_successes, sim_trials):
            if t == 0:
                sim_s.append(0)
                sim_t.append(0)
                continue
            x = sum(1 for _ in range(t) if rng.random() < (s / t))
            sim_s.append(x)
            sim_t.append(t)
        wt_s = []
        wt_t = []
        for s, t in zip(wt_successes, wt_trials):
            if t == 0:
                wt_s.append(0)
                wt_t.append(0)
                continue
            x = sum(1 for _ in range(t) if rng.random() < (s / t))
            wt_s.append(x)
            wt_t.append(t)
        draws.append(overall_rate(sim_s, sim_t) - overall_rate(wt_s, wt_t))
    draws.sort()
    lo = draws[int(0.025 * reps)]
    hi = draws[int(0.975 * reps)]
    return (obs_diff, lo, hi)


def permutation_test_stratified(
    cases: list[dict], mode_key: str, outcome_key: str, stratum_key: str,
    reps: int = PERM_REPS, seed: int = SEED,
) -> tuple[float, float]:
    """
    Permute mode labels WITHIN each stratum (repo). Compute observed overall
    rate difference (Sim - WT) and two-sided p-value.
    """
    rng = random.Random(seed)

    def diff(rows: list[dict]) -> float:
        sim_s = sim_t = wt_s = wt_t = 0
        for r in rows:
            if r[mode_key] == "Simulation":
                sim_t += 1
                sim_s += 1 if r[outcome_key] else 0
            elif r[mode_key] == "WallTime":
                wt_t += 1
                wt_s += 1 if r[outcome_key] else 0
        sim = sim_s / sim_t if sim_t else 0.0
        wt = wt_s / wt_t if wt_t else 0.0
        return sim - wt

    obs = diff(cases)

    # Group by stratum
    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for r in cases:
        by_stratum[r[stratum_key]].append(r)

    extreme = 0
    for _ in range(reps):
        permuted: list[dict] = []
        for stratum, rows in by_stratum.items():
            modes = [r[mode_key] for r in rows]
            rng.shuffle(modes)
            for r, m in zip(rows, modes):
                rr = dict(r)
                rr[mode_key] = m
                permuted.append(rr)
        d = diff(permuted)
        if abs(d) >= abs(obs):
            extreme += 1
    p_two = extreme / reps
    return obs, p_two


# ---------- main ----------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = [json.loads(l) for l in ATLAS_PATH.read_text().splitlines() if l.strip()]

    # Normalize fields
    for c in cases:
        c["mode"] = c.get("_mode") or "Unknown"
        out = c.get("_outcome", "unknown")
        c["merged_after_flag"] = out in ("merged_as_is", "merged_with_fix")
        c["merged_as_is"] = out == "merged_as_is"
        comment_created = c.get("comment_created_at") or ""
        try:
            dt = datetime.fromisoformat(comment_created.replace("Z", "+00:00"))
            c["era"] = "post_launch" if dt >= LAUNCH_DATE else "pre_launch"
            c["comment_dt"] = dt.isoformat()
        except Exception:
            c["era"] = "unknown"

    total = len(cases)
    print(f"Loaded {total} cases from {ATLAS_PATH}")
    print()

    # --- Section 1: Era split ---
    era_counts = Counter(c["era"] for c in cases)
    print("=== Era split (codspeed-optimize launch = 2026-03-16) ===")
    for era, n in era_counts.items():
        print(f"  {n:3d}  {era}")
    print()

    # --- Section 2: Per-case mode × outcome contingency (headline) ---
    mode_outcome = Counter()
    for c in cases:
        mode_outcome[(c["mode"], c["merged_after_flag"])] += 1
    print("=== Mode × outcome contingency (all cases) ===")
    print(f"  Sim  merged_after_flag:    {mode_outcome[('Simulation', True)]:3d}")
    print(f"  Sim  not_merged:           {mode_outcome[('Simulation', False)]:3d}")
    print(f"  WT   merged_after_flag:    {mode_outcome[('WallTime', True)]:3d}")
    print(f"  WT   not_merged:           {mode_outcome[('WallTime', False)]:3d}")
    print()

    # --- Section 3: Wilson CIs on per-mode rate ---
    print("=== Per-mode merge-after-flag rate with Wilson 95% CIs ===")
    for mode in ("Simulation", "WallTime"):
        sub = [c for c in cases if c["mode"] == mode]
        n = len(sub)
        s = sum(1 for c in sub if c["merged_after_flag"])
        rate = s / n if n else 0
        lo, hi = wilson_ci(s, n)
        print(f"  {mode:12s}  n={n:3d}  rate={rate:.3f}  95% CI=[{lo:.3f}, {hi:.3f}]")
    print()

    # --- Section 4: Repo-stratified permutation test (headline test) ---
    modal_cases = [c for c in cases if c["mode"] in ("Simulation", "WallTime")]
    obs_diff, p_two = permutation_test_stratified(
        modal_cases, mode_key="mode", outcome_key="merged_after_flag",
        stratum_key="repo",
    )
    print("=== HEADLINE TEST: Repo-stratified permutation test ===")
    print(f"  Mode label permuted within each repo's case set")
    print(f"  Observed (Sim rate) − (WT rate) = {obs_diff:+.4f}")
    print(f"  Two-sided p-value (n={PERM_REPS} perms) = {p_two:.4f}")
    print()

    # --- Section 5: Bootstrap 95% CI on rate difference ---
    sim_by_repo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    wt_by_repo: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in modal_cases:
        bucket = sim_by_repo if c["mode"] == "Simulation" else wt_by_repo
        bucket[c["repo"]][1] += 1
        if c["merged_after_flag"]:
            bucket[c["repo"]][0] += 1
    repos = sorted(set(list(sim_by_repo.keys()) + list(wt_by_repo.keys())))
    sim_s = [sim_by_repo[r][0] for r in repos]
    sim_t = [sim_by_repo[r][1] for r in repos]
    wt_s = [wt_by_repo[r][0] for r in repos]
    wt_t = [wt_by_repo[r][1] for r in repos]
    point, lo95, hi95 = bootstrap_rate_diff_ci(sim_s, sim_t, wt_s, wt_t)
    print("=== Bootstrap 95% CI on rate difference ===")
    print(f"  Point estimate (Sim − WT): {point:+.4f}")
    print(f"  95% CI: [{lo95:+.4f}, {hi95:+.4f}]")
    print()

    # --- Section 6: Per-repo descriptive table (n<PER_REPO_REPORT_FLOOR → raw only) ---
    print(f"=== Per-repo table (suppress rate if n<{PER_REPO_REPORT_FLOOR}) ===")
    repo_stats = []
    for repo in sorted({c["repo"] for c in cases}):
        sub = [c for c in cases if c["repo"] == repo]
        n = len(sub)
        merged = sum(1 for c in sub if c["merged_after_flag"])
        merged_as_is = sum(1 for c in sub if c["merged_as_is"])
        if n >= PER_REPO_REPORT_FLOOR:
            lo, hi = wilson_ci(merged, n)
            rate_str = f"{merged/n:.3f}  [{lo:.3f}, {hi:.3f}]"
        else:
            rate_str = "(n too small, raw only)"
        repo_stats.append({"repo": repo, "n": n, "merged": merged,
                           "merged_as_is": merged_as_is, "rate_str": rate_str})
        print(f"  {repo:30s}  n={n:3d}  merged={merged:2d} (as-is={merged_as_is:2d})  {rate_str}")
    print()

    # --- Section 7: Era split × mode (recency confound disclosure) ---
    print("=== Era × Mode × Merge-after-flag (disclose recency confound) ===")
    for era in ("pre_launch", "post_launch"):
        for mode in ("Simulation", "WallTime"):
            sub = [c for c in modal_cases if c["era"] == era and c["mode"] == mode]
            n = len(sub)
            s = sum(1 for c in sub if c["merged_after_flag"])
            if n >= 5:
                rate = s / n
                lo, hi = wilson_ci(s, n)
                print(f"  {era:12s}  {mode:10s}  n={n:3d}  rate={rate:.3f}  [{lo:.3f}, {hi:.3f}]")
            else:
                print(f"  {era:12s}  {mode:10s}  n={n:3d}  (too small)")
    print()

    # --- Section 8: FOCAL within-repo mode comparison (ruff) ---
    print("=== FOCAL within-repo mode comparison (ruff) ===")
    print("    (only repo with mixed Sim+WT cases → only context where")
    print("     mode effect is identifiable without confounding)")
    focal = [c for c in cases if c["repo"] == "astral-sh/ruff" and c["mode"] in ("Simulation", "WallTime")]
    focal_sim = [c for c in focal if c["mode"] == "Simulation"]
    focal_wt = [c for c in focal if c["mode"] == "WallTime"]
    fs_merged = sum(1 for c in focal_sim if c["merged_after_flag"])
    fw_merged = sum(1 for c in focal_wt if c["merged_after_flag"])
    print(f"  Sim n={len(focal_sim)}  merged={fs_merged}/{len(focal_sim)}  rate={fs_merged/len(focal_sim):.3f}  CI={wilson_ci(fs_merged, len(focal_sim))}")
    print(f"  WT  n={len(focal_wt)}  merged={fw_merged}/{len(focal_wt)}  rate={fw_merged/len(focal_wt):.3f}  CI={wilson_ci(fw_merged, len(focal_wt))}")

    # Fisher's exact test on 2x2 (Sim merged / Sim not / WT merged / WT not)
    try:
        from math import comb
        def fisher_p_two(a: int, b: int, c: int, d: int) -> float:
            # 2x2:  |  merged  not  |
            #  sim  |    a     b    |
            #  wt   |    c     d    |
            n = a + b + c + d
            row1, row2 = a + b, c + d
            col1 = a + c
            observed = comb(row1, a) * comb(row2, c) / comb(n, col1)
            p = 0.0
            for x in range(max(0, col1 - row2), min(col1, row1) + 1):
                candidate = comb(row1, x) * comb(row2, col1 - x) / comb(n, col1)
                if candidate <= observed + 1e-12:
                    p += candidate
            return p
        fp = fisher_p_two(fs_merged, len(focal_sim) - fs_merged,
                          fw_merged, len(focal_wt) - fw_merged)
        print(f"  Fisher's exact (2-sided) p-value: {fp:.4f}")
    except Exception as e:
        print(f"  Fisher's exact failed: {e}")

    # Permutation test on rate difference within ruff
    rng = random.Random(SEED)
    def rate_diff_subset(subset: list[dict]) -> float:
        s = [c for c in subset if c["mode"] == "Simulation"]
        w = [c for c in subset if c["mode"] == "WallTime"]
        if not s or not w:
            return 0.0
        rs = sum(1 for c in s if c["merged_after_flag"]) / len(s)
        rw = sum(1 for c in w if c["merged_after_flag"]) / len(w)
        return rs - rw
    obs_focal = rate_diff_subset(focal)
    extreme_focal = 0
    modes_list = [c["mode"] for c in focal]
    for _ in range(PERM_REPS):
        shuffled = modes_list.copy()
        rng.shuffle(shuffled)
        tmp = [dict(c, mode=m) for c, m in zip(focal, shuffled)]
        d = rate_diff_subset(tmp)
        if abs(d) >= abs(obs_focal):
            extreme_focal += 1
    p_focal = extreme_focal / PERM_REPS
    print(f"  Permutation test (within-repo, n={PERM_REPS}): observed Sim−WT = {obs_focal:+.4f}, p = {p_focal:.4f}")
    print()

    # --- Save structured JSON for report.md consumption ---
    output = {
        "n_cases_total": total,
        "launch_date": LAUNCH_DATE.isoformat(),
        "era_counts": dict(era_counts),
        "mode_outcome_contingency": {
            f"{m}_{o}": mode_outcome[(m, o)]
            for m in ("Simulation", "WallTime", "Unknown")
            for o in (True, False)
        },
        "headline_test": {
            "test_name": "repo-stratified permutation test of merge-after-flag rate, Sim vs WT",
            "observed_rate_diff_sim_minus_wt": obs_diff,
            "two_sided_p_value": p_two,
            "permutations": PERM_REPS,
            "bootstrap_point": point,
            "bootstrap_ci_lo95": lo95,
            "bootstrap_ci_hi95": hi95,
            "bootstrap_reps": BOOTSTRAP_REPS,
        },
        "per_repo": repo_stats,
        "per_mode_wilson": {
            mode: {
                "n": len([c for c in cases if c["mode"] == mode]),
                "merged": sum(1 for c in cases if c["mode"] == mode and c["merged_after_flag"]),
                "rate": sum(1 for c in cases if c["mode"] == mode and c["merged_after_flag"]) / max(1, len([c for c in cases if c["mode"] == mode])),
                "wilson95": list(wilson_ci(
                    sum(1 for c in cases if c["mode"] == mode and c["merged_after_flag"]),
                    len([c for c in cases if c["mode"] == mode]),
                )),
            }
            for mode in ("Simulation", "WallTime", "Unknown")
        },
    }
    out_json = OUT_DIR / "analysis_results.json"
    out_json.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Wrote structured results to {out_json}")


if __name__ == "__main__":
    main()
