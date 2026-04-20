# CI Regression Atlas — Executive Summary

*An independent empirical study of how engineering teams handle CI
performance regression flags, built on the public `codspeed-hq[bot]`
comment history across eleven OSS repositories.*

## What this is

An external analysis of the `codspeed-hq[bot]` performance comment
history across 11 public OSS customer repositories (Next.js, LangChain,
FastAPI, Pydantic, Ruff, uv, Biome, Astro, Turborepo, Turso,
pydantic-core). The full dataset: **13,490 bot comments crawled**,
distilled into a **65-case diagnostic set** stratified by language,
regression magnitude, and outcome.

All data is public (GitHub REST API). All analysis code is
zero-dependency stdlib Python.

---

## Three empirical findings

### Finding 1 — Merge-after-flag rate varies by 40+ percentage points across customers

Of the 65 flagged PRs, the outcome distribution:

- **24.6% merged_as_is** (flag ignored, PR merged unchanged)
- **24.6% merged_with_fix** (flag acknowledged, fix added before merge)
- **29.2% abandoned**
- **21.5% open at snapshot time**

The "merge-after-flag" rate (combining merged_as_is and merged_with_fix)
varies dramatically by customer:

| Repo | Cases | Merge-after-flag | (as_is / with_fix) |
|------|-------|------------------|--------------------|
| tursodatabase/turso | 5 | 80% | (0 / 4) |
| pydantic/pydantic | 13 | 62% | (6 / 2) |
| vercel/next.js | 9 | 56% | (0 / 5) |
| langchain-ai/langchain | 9 | 44% | (4 / 0) |
| astral-sh/ruff | 18 | 22% | (2 / 2) |

**Reportable spread: 39.3 percentage points** (pydantic 62% − ruff 22%,
both n ≥ 8). The `(as_is / with_fix)` split reveals distinct customer
behaviors: turso and next.js *always fix* before merging; langchain
*always merges as-is* when it merges; pydantic mixes both. A single
global flagging threshold fits no customer's bar.

### Finding 2 — Within the diagnostic set, five benchmarks drive 45% of all flagged cases

29 of the 65 cases (44.6%) concentrate on five specific benchmarks
across four customer repos:

| Count | Benchmark | Repo |
|-------|-----------|------|
| 8 | `test_async_callbacks_in_sync` | langchain |
| 6 | `test_simple_recursive_model_schema_generation` | pydantic |
| 6 | `packages-bundle.js[full]` | next.js |
| 5 | `colour_science` | ruff |
| 4 | `test_validators_build` | pydantic |

**Selection-bias disclosure**: the picker favors single-bench PRs, and
91% of the set has exactly one regressed benchmark. Fleet-wide
concentration in the full 13,490-comment corpus is very likely lower.
The concentration is still informative *within this subsample* — the
same benchmarks recurring across independent PRs is not a sampling
artifact — but the headline percentage is not a fleet-wide statistic.

Each recurring benchmark is one of three things: a persistent signal
the team tolerates as "known noise," genuine instability needing
rework, or a high-leverage target for agent intervention. The data
surfaces *where to look*; the call of which is a product judgment.

### Finding 3 — Response latency is bimodal; 19% of flagged PRs merge in under 15 minutes

For the 42 cases with measurable comment-to-close latency:

| Percentile | Latency |
|------------|---------|
| p25 | 41 min |
| p50 | 9.0 hours |
| p75 | 44.6 hours |
| p90 | 4.2 days |

- **Fast tier (19%, n=8)**: merged within 15 min of the bot comment
- **Normal tier (45%, n=19)**: 15 min to 1 day
- **Investigation tier (29%, n=12)**: 1 to 7 days
- **Long-tail (7%, n=3)**: over 7 days

The 19% fast tier is too fast to reflect investigation. Actual titles:
5 of 8 fit a "bypass-review" pattern (3 dep/version bumps + 2 docs-only
changes); the remaining 3 are substantive (feature add, revert,
new-benchmark-file) that merged quickly nonetheless.

Either way, a fix-suggesting agent has a narrow window with this
tier. One-size-fits-all agent UX under-serves one of these latency
bands.

*Percentiles use nearest-rank indexing; reproducing with
`numpy.percentile` will yield slightly different values.*

---

## Companion: safety audit (supplementary)

A separate static audit of `skills/codspeed-optimize/SKILL.md`
identifies four reward-hacking surfaces in the open agent
specification (benchmark file modification, `[profile.bench]`
editing, test suppression, sub-noise-floor chasing) and ships a
**377-line `variance_gate.py`** prototype closing all four with
nine passing smoke tests. This is a safety layer for future
agent-driven optimizers. See `findings.md` for details.

---

## Reproducibility

Every number derivable from:
- `scripts/crawl.py` — GitHub Search API crawler (13,490 comments)
- `scripts/parse.py` — bot-comment regex parser
- `scripts/pick.py` — 65-case diagnostic sampler
- `scripts/analyze.py` — repo-stratified permutation tests + bootstrap CI
- `scripts/product_findings.py` — the three findings above
- `scripts/variance_gate.py` — the safety-audit prototype

All stdlib Python, one optional `matplotlib` dep for figure generation.
