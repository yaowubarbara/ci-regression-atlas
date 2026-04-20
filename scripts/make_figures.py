"""Generate the three figures referenced in REPORT.md.

Inputs: labeled/analysis_results.json, labeled/diagnostic_set.jsonl
Outputs: figures/fig1_forest.png
         figures/fig2_bootstrap.png
         figures/fig3_era_mode.png
"""

from __future__ import annotations

import json
import pathlib
import random
from collections import defaultdict
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path("/home/dev/codspeed-atlas")
FIG = ROOT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "savefig.dpi": 160,
    "figure.dpi": 160,
})

results = json.loads((ROOT / "labeled" / "analysis_results.json").read_text())


# --------- Fig 1: Forest plot of per-repo merge-after-flag rate ---------
def fig1_forest() -> None:
    per_repo = results["per_repo"]
    # Only include n>=8 for rate-testable subset
    testable = [r for r in per_repo if r["n"] >= 8]
    testable.sort(key=lambda r: r["n"], reverse=True)
    # Compute Wilson CIs we can read from rate_str
    import math
    def wilson(s: int, n: int, z: float = 1.96) -> tuple[float, float]:
        if n == 0:
            return 0.0, 0.0
        p = s / n
        denom = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denom
        half = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
        return max(0.0, center - half), min(1.0, center + half)

    labels = [r["repo"].split("/")[-1] for r in testable]
    rates = [r["merged"] / r["n"] for r in testable]
    cis = [wilson(r["merged"], r["n"]) for r in testable]
    ns = [r["n"] for r in testable]

    fig, ax = plt.subplots(figsize=(7.2, 2.6), tight_layout=True)
    y = list(range(len(labels)))
    for i, (rate, (lo, hi), n) in enumerate(zip(rates, cis, ns)):
        ax.errorbar(rate, i, xerr=[[rate - lo], [hi - rate]],
                    fmt="o", color="#0057b7", markersize=6,
                    capsize=4, capthick=1.2, linewidth=1.5)
        ax.text(1.02, i, f"n={n}", va="center", fontsize=8, color="#555")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, 1.1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("merge-after-flag rate (Wilson 95% CI)")
    ax.set_title("Per-repo merge-after-flag rate, repos with n≥8", fontsize=10)
    ax.axvline(0.5, color="#bbb", linestyle="--", linewidth=0.8)
    ax.invert_yaxis()
    fig.savefig(FIG / "fig1_forest.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {FIG / 'fig1_forest.png'}")


# --------- Fig 2: Bootstrap distribution of Sim-WT rate difference ---------
def fig2_bootstrap() -> None:
    # Re-derive the bootstrap draws (stored point + CI in JSON; regenerate draws for hist)
    cases = [json.loads(l) for l in (ROOT / "labeled" / "diagnostic_set.jsonl").read_text().splitlines() if l.strip()]
    for c in cases:
        c["mode"] = c.get("_mode") or "Unknown"
        c["merged_after_flag"] = c.get("_outcome") in ("merged_as_is", "merged_with_fix")
    modal = [c for c in cases if c["mode"] in ("Simulation", "WallTime")]
    sim_by_repo = defaultdict(lambda: [0, 0])
    wt_by_repo = defaultdict(lambda: [0, 0])
    for c in modal:
        bucket = sim_by_repo if c["mode"] == "Simulation" else wt_by_repo
        bucket[c["repo"]][1] += 1
        if c["merged_after_flag"]:
            bucket[c["repo"]][0] += 1
    repos = sorted(set(list(sim_by_repo) + list(wt_by_repo)))

    rng = random.Random(42)
    draws = []
    for _ in range(10_000):
        sim_s = sim_t = wt_s = wt_t = 0
        for r in repos:
            s1, t1 = sim_by_repo[r]
            if t1:
                sim_s += sum(1 for _ in range(t1) if rng.random() < s1 / t1)
                sim_t += t1
            s2, t2 = wt_by_repo[r]
            if t2:
                wt_s += sum(1 for _ in range(t2) if rng.random() < s2 / t2)
                wt_t += t2
        rs = sim_s / sim_t if sim_t else 0
        rw = wt_s / wt_t if wt_t else 0
        draws.append(rs - rw)

    point = results["headline_test"]["bootstrap_point"]
    lo = results["headline_test"]["bootstrap_ci_lo95"]
    hi = results["headline_test"]["bootstrap_ci_hi95"]

    fig, ax = plt.subplots(figsize=(7.2, 2.8), tight_layout=True)
    ax.hist(draws, bins=45, color="#0057b7", alpha=0.7, edgecolor="white")
    ax.axvline(0, color="#000", linewidth=1.2, linestyle="-")
    ax.axvline(point, color="#d43f3f", linewidth=1.5, linestyle="-", label=f"point = {point:+.3f}")
    ax.axvline(lo, color="#d43f3f", linewidth=1, linestyle="--", label=f"95% CI [{lo:+.3f}, {hi:+.3f}]")
    ax.axvline(hi, color="#d43f3f", linewidth=1, linestyle="--")
    ax.set_xlabel("Simulation rate − WallTime rate  (bootstrap, 10k draws)")
    ax.set_ylabel("frequency")
    ax.set_title("Bootstrap distribution of inter-mode rate gap — CI crosses zero",
                 fontsize=10)
    ax.legend(loc="upper right", frameon=False, fontsize=8)
    fig.savefig(FIG / "fig2_bootstrap.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {FIG / 'fig2_bootstrap.png'}")


# --------- Fig 3: Era × Mode 2x2 bar chart ---------
def fig3_era_mode() -> None:
    cases = [json.loads(l) for l in (ROOT / "labeled" / "diagnostic_set.jsonl").read_text().splitlines() if l.strip()]
    launch = datetime(2026, 3, 16, tzinfo=timezone.utc)
    data: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for c in cases:
        mode = c.get("_mode")
        if mode not in ("Simulation", "WallTime"):
            continue
        cd = c.get("comment_created_at") or ""
        try:
            dt = datetime.fromisoformat(cd.replace("Z", "+00:00"))
        except Exception:
            continue
        era = "post" if dt >= launch else "pre"
        data[(era, mode)][1] += 1
        if c.get("_outcome") in ("merged_as_is", "merged_with_fix"):
            data[(era, mode)][0] += 1

    eras = ["pre", "post"]
    modes = ["Simulation", "WallTime"]
    rates = {(e, m): (data[(e, m)][0] / data[(e, m)][1] if data[(e, m)][1] else 0) for e in eras for m in modes}
    ns = {(e, m): data[(e, m)][1] for e in eras for m in modes}

    fig, ax = plt.subplots(figsize=(6.4, 3.0), tight_layout=True)
    width = 0.35
    x = list(range(len(eras)))
    sim_heights = [rates[(e, "Simulation")] for e in eras]
    wt_heights = [rates[(e, "WallTime")] for e in eras]
    sim_bars = ax.bar([p - width / 2 for p in x], sim_heights, width,
                       label="Simulation", color="#0057b7")
    wt_bars = ax.bar([p + width / 2 for p in x], wt_heights, width,
                     label="WallTime", color="#d43f3f")
    for i, e in enumerate(eras):
        for bar, mode, offset in ((sim_bars[i], "Simulation", -width/2),
                                   (wt_bars[i], "WallTime", width/2)):
            n = ns[(e, mode)]
            if n > 0:
                ax.text(i + offset, bar.get_height() + 0.02,
                        f"n={n}", ha="center", fontsize=8, color="#333")
            else:
                ax.text(i + offset, 0.02,
                        "n=0", ha="center", fontsize=8, color="#888", style="italic")

    ax.set_xticks(x)
    ax.set_xticklabels(["pre-launch\n(before 2026-03-16)",
                        "post-launch\n(2026-03-16 onwards)"])
    ax.set_ylabel("merge-after-flag rate")
    ax.set_ylim(0, 1.0)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_title("merge-after-flag rate by era × mode (recency confound disclosure)",
                 fontsize=10)
    ax.legend(frameon=False, fontsize=9)
    fig.savefig(FIG / "fig3_era_mode.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {FIG / 'fig3_era_mode.png'}")


if __name__ == "__main__":
    fig1_forest()
    fig2_bootstrap()
    fig3_era_mode()
