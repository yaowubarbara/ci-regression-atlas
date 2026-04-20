# CI Regression Atlas
## Empirical patterns in how CI performance regressions get handled in the wild

This repository is an independent empirical study of how engineering
teams respond to automated CI performance regression flags. The
primary dataset is the public `codspeed-hq[bot]` comment history across
eleven widely-used OSS repositories (Next.js, LangChain, FastAPI,
Pydantic, Ruff, uv, Biome, Astro, Turborepo, Turso, pydantic-core).
The question it answers: when a bot flags a regression, what actually
happens? Which PRs get merged anyway, which benchmarks recur, and how
fast do teams move after a flag?

It contains:
- a dataset of **13,490 bot comments** across **11 OSS repos**,
- a **65-case diagnostic set** stratified by language, magnitude, outcome,
- three empirical findings on outcome variance, benchmark recurrence,
  and response latency,
- a supplementary static audit of the `codspeed-optimize` skill
  specification (`skills/codspeed-optimize/SKILL.md`) surfacing four
  reward-hacking surfaces, with a 377-line `variance_gate.py`
  mitigation prototype (§3),
- a one-page methodology companion mapping the findings to
  verifiable-reward principles from the public RLVR literature
  (`rlvr_mapping.md`).

All data is public (GitHub REST API). All code is zero-dependency
stdlib Python. No affiliation with CodSpeed or any surveyed
repository.

---

## TL;DR — three empirical findings

### Finding 1: Merge-after-flag rate varies by 58 percentage points across customers

In the 65 case set, outcomes break down as:

- `merged_as_is` (flag ignored, PR merged unchanged): **16 cases (24.6%)**
- `merged_with_fix` (flag acknowledged, fix added before merge): **16 cases (24.6%)**
- `abandoned` (PR closed without merging): **19 cases (29.2%)**
- `open` (still open at snapshot time): **14 cases (21.5%)**

"Merge-after-flag" below refers to both `merged_as_is` and `merged_with_fix`
combined (32/65 = 49.2%). This aggregates two different team behaviors
(ignoring the flag vs. responding to it) and the per-repo split is
informative about which mode dominates for a given customer:

| Repo | Cases | Merge-after-flag | (as_is / with_fix) |
|------|-------|------------------|--------------------|
| tursodatabase/turso | 5 | **80%** (4/5) | (0 / 4) |
| pydantic/pydantic | 13 | **62%** (8/13) | (6 / 2) |
| vercel/next.js | 9 | **56%** (5/9) | (0 / 5) |
| langchain-ai/langchain | 9 | **44%** (4/9) | (4 / 0) |
| astral-sh/ruff | 18 | **22%** (4/18) | (2 / 2) |

**Statistical caveat**: turso's n = 5 is below the n ≥ 8 reportability
threshold §2 applies to per-repo rates (Wilson 95% CI for turso =
[37.6%, 96.4%], wide enough to overlap ruff's upper CI). The point
estimate 58pp is therefore a **ceiling**; the robust floor, using only
repos with n ≥ 8, is **pydantic 62% − ruff 22% = 40pp**. Either way,
the spread is **an order of magnitude larger than the within-repo
Simulation-vs-WallTime mode effect** the data can identify (ruff
within-repo: −3.1pp, p = 1.0; see §2).

The `(as_is / with_fix)` split additionally reveals distinct customer
behaviors that neither Sim/WT mode nor a single rate number captures:
turso and next.js **always fix** before merging (0 as_is across 9
combined cases), langchain **always merges as-is** when it merges
(4/4), pydantic **mixes both**. **Practical implication**: a global
flagging threshold fits no single customer's bar, and a per-repo
signal trust score — calibrated on whether a team tends to fix flags
or ignore them — is a stronger foundation for agent-driven
intervention than uniform thresholds.

### Finding 2: Within the 65-case diagnostic set, five benchmarks account for 45% of the cases

Across the 65 cases, five specific benchmarks across four customer
repos appear in 29 of the 65 cases (44.6%):

| Count | Benchmark | Repo |
|-------|-----------|------|
| 8 | `test_async_callbacks_in_sync` | langchain-ai/langchain |
| 6 | `test_simple_recursive_model_schema_generation` | pydantic/pydantic |
| 6 | `packages-bundle.js[full]` | vercel/next.js |
| 5 | `colour_science` | astral-sh/ruff |
| 4 | `test_validators_build` | pydantic/pydantic |

**Selection-bias disclosure**: `scripts/pick.py` explicitly scores
single-regressed-benchmark PRs above multi-bench ones, and **59 of the
65 cases (91%) have exactly one regressed benchmark**. The 45%
concentration is therefore partly a product of the sampling rule.
Fleet-wide concentration in the full 13,490-comment corpus is very
likely lower. The concentration is still informative *within this
subsample* — the same per-repo benchmark showing up 4–8 independent
times across distinct PRs is not a sampling artifact — but the
headline percentage should not be read as a fleet-wide statistic.

Each of these benchmarks is one of three things, and the data does
not yet distinguish which: (a) a high-value signal the team has
learned to tolerate as "known noise," (b) a genuinely unstable
benchmark that should be reworked, or (c) a high-leverage target for
a future `codspeed-optimize` focus. **Practical implication**: these
five are candidate anchor targets for the next skill iteration; the
data surfaces *where to look*, not *what to do*.

### Finding 3: Response latency is bimodal — 19% of merges happen within 15 minutes

For 42 of the 65 cases where comment-to-close latency is measurable:

| Percentile | Latency |
|------------|---------|
| p25 | 41 min |
| p50 | 9.0 hours |
| p75 | 44.6 hours |
| p90 | 4.2 days |

Bucketed:

- **Fast tier (19%, n=8)**: merged within 15 min of the bot comment
- **Normal tier (45%, n=19)**: 15 min to 1 day
- **Investigation tier (29%, n=12)**: 1 to 7 days
- **Long-tail (7%, n=3)**: over 7 days

The 19% fast tier is **too fast to be investigation** — either the
team treats the flag as noise, or the PR type bypasses human review.
Actual titles of the 8 fast-tier PRs (all merged):

| Latency | PR | Title |
|---------|----|----|
| 2.0 min | astral-sh/uv #18521 | Bump version to 0.10.11 |
| 2.1 min | langchain-ai/langchain #36510 | feat(core): add `ChatBaseten` to serializable mapping |
| 3.1 min | pydantic/pydantic #13059 | 👥 Update Pydantic People |
| 4.3 min | pydantic/pydantic #13046 | Include everyone in people page |
| 4.6 min | pydantic/pydantic #13061 | Add basic benchmarks for model equality |
| 5.2 min | pydantic/pydantic #13080 | Bump pillow from 10.4.0 to 12.2.0 |
| 6.7 min | langchain-ai/langchain #36322 | revert: Revert "fix(core): trace invocation params in metadata" |
| 12.2 min | pydantic/pydantic #13064 | Update jiter to v0.14.0 |

**5 of 8 fit the "bypass-review" pattern** (3 dep/version bumps +
2 docs-only changes). The remaining 3 are genuinely fast merges of
substantive changes (feature add, revert, new-benchmark-file). So
the "fast tier" is a mix of bypass-class PRs and legitimately
quick human decisions — not a uniform "flag was ignored" tier.
Either way, a fix-suggesting agent has a narrow window with this
tier. **Product
implication**: a single agent UX fits no latency tier. Fast-tier
teams need noise suppression, PR-type-aware silencing, or confidence
gating; investigation-tier teams can consume deeper diagnostics and
proposed patches. Latency percentiles use nearest-rank indexing
(`int(p·(n−1)/100)`), not linear interpolation, so reproducing the
p25/p50/p75/p90 with `numpy.percentile` will yield slightly different
values.

Each finding points to a concrete agentic-feature direction. Findings
with tickets in §6.

---

The static audit of `skills/codspeed-optimize/SKILL.md` (four
reward-hacking paths with `variance_gate.py` mitigation) is retained
as §3. It remains relevant as a safety layer for any future
agent-driven optimizer, but the empirical findings above are the
primary reading path.

---

## §1. The Atlas

### Construction

Phase A: crawl `codspeed-hq[bot]` comment metadata across 11 public
OSS customer repos, yielding 13,490 PR records.

Phase B: enrich up to 150 PRs per repo (newest-first) by fetching the
bot comment body and PR details via GitHub API. Early-stop per repo
after 15 confirmed regressions (or 45 for the focal repo `ruff`).

Phase C: apply a multi-constraint picker — minimum 2 per regression
repo, minimum 22 WallTime cases overall, and focal within-repo mode
diversity for `ruff` (≥5 Simulation + ≥5 WallTime). Output: 65 cases
across 9 repos, stratified by language (22 Python / 31 Rust / 12 TS),
magnitude (≥20%, 10-20%, <10%), outcome class (merged-as-is,
merged-with-fix, abandoned, open), and mode.

### Distribution

| Repo | n | Mode composition | Ownership |
|---|---|---|---|
| astral-sh/ruff | 18 | WallTime 13 + Simulation 5 | JD-named customer (Astral) |
| pydantic/pydantic | 13 | Simulation 13 | JD-named customer |
| langchain-ai/langchain | 9 | WallTime 9 | public CodSpeed OSS user |
| vercel/next.js | 9 | Simulation 9 | JD-named customer (Vercel) |
| tursodatabase/turso | 5 | Simulation 5 | public CodSpeed OSS user |
| astral-sh/uv | 4 | Simulation 4 | JD-named customer (Astral) |
| withastro/astro | 3 | Simulation 3 | public CodSpeed OSS user |
| biomejs/biome | 2 | mode-undetermined 2 | public CodSpeed OSS user |
| pydantic/pydantic-core | 2 | Simulation 2 | JD-named customer |
| **total** | **65** | Sim 41 / WT 22 / Unknown 2 | |

Two repos were crawled but yielded zero regressions flagged in the
scanned window: `fastapi/fastapi` (150 scanned, 0 regressions) and
`vercel/turborepo` (100 scanned, 0 regressions). This is itself
informative: the bot's flagging rate across its OSS customer base
ranges from 0% to 48% (pydantic).

Source: `labeled/diagnostic_set.jsonl` (65 rows) + `labeled/summary.md`
(human-readable table).

---

## §2. Mode vs repo-culture

### Headline test

Unit of analysis: per-case. Strata: repo (9 repos with ≥1 regression
in the Atlas).

Repo-stratified permutation test of merge-after-flag rate, Simulation
vs WallTime, n = 10,000 permutations:

```
Observed Sim − WT = +0.2428  (+24.28 percentage points)
Two-sided p-value = 0.7690
Bootstrap 95% CI (stratified by repo) = [+0.0122, +0.4734]
```

The observed +24.3pp gap has a CI that does not cross zero, but the
permutation p-value is 0.77 — the two statistics disagree because only
1 repo (ruff) contributes genuine within-stratum permutation power.
The other 10 repos are single-mode, so within-stratum permutation is
mechanically degenerate there.

**Bootstrap distribution** — see `figures/fig2_bootstrap.png`.

### Within-repo (the only identifiable comparison)

ruff is the only mixed-mode repo in the Atlas (18 cases: 13 WallTime +
5 Simulation). Within ruff:

```
Simulation merge-after-flag rate: 20.0% (1/5)   Wilson 95% CI [3.6%, 62.4%]
WallTime   merge-after-flag rate: 23.1% (3/13)  Wilson 95% CI [8.2%, 50.3%]
Difference: -3.1pp
Fisher's exact (two-sided) p-value = 1.0
```

**When repo culture is controlled, no mode effect is detectable.**

### Per-repo variance is what the data supports

Per-repo merge-after-flag rates, reportable only for n ≥ 8:

```
ruff          22.2%  (4/18)  Wilson 95% CI [9.0%, 45.2%]   ← lowest reportable
langchain     44.4%  (4/9)   Wilson 95% CI [18.9%, 73.3%]
next.js       55.6%  (5/9)   Wilson 95% CI [26.7%, 81.1%]
pydantic      61.5%  (8/13)  Wilson 95% CI [35.5%, 82.3%]  ← highest reportable
```

**Reportable spread: 39.3 percentage points** (pydantic 61.5% − ruff
22.2%), still an order of magnitude larger than the within-repo mode
effect (−3.1pp within ruff). Non-reportable repos (turso n=5, uv n=4,
astro n=3, biome n=2, pydantic-core n=2) have raw rates of 80%, 75%,
0%, 100%, 100% respectively — the CIs are too wide to anchor any
comparison, but the raw values are consistent with the reportable
spread and extend it in both directions. See
`figures/fig1_forest.png`.

### Recency confound disclosure

codspeed-optimize shipped 2026-03-16. 50 of 65 Atlas cases are
post-launch, 15 are pre-launch. Splitting further by mode:

```
pre-launch   Simulation  n= 9  rate=66.7%  [35.4%, 87.9%]
pre-launch   WallTime    n= 6  rate=16.7%  [ 3.0%, 56.4%]
post-launch  Simulation  n=32  rate=53.1%  [36.4%, 69.1%]
post-launch  WallTime    n=16  rate=37.5%  [18.5%, 61.4%]
```

See `figures/fig3_era_mode.png`. The pre-launch WallTime cell (n=6)
is under-sampled and its 16.7% point estimate has a Wilson CI
spanning [3.0%, 56.4%]; any time-trend claim requires a larger
sample.

### What the data cannot answer (by construction)

Because of the mode-repo confound and the small within-repo sample
for the only mixed-mode repo, the Atlas cannot:

- detect a mode effect on intra-run variance (bot comments report
  deltas, not intra-run σ),
- isolate team-culture effect from mode-selection effect,
- establish causation for the observed per-benchmark concentration
  (e.g., langchain's `test_async_callbacks_in_sync` pattern).

Addressing these would require either (a) CodSpeed internal intra-run
variance data, or (b) over-sampling mixed-mode repos.

---

## §3. SKILL.md audit

Full findings are in `findings.md`. Four findings, each with exact
SKILL.md citation, hypothetical failure scenario, and proposed
mitigation:

- **A1 — Optimization scope is not constrained to production code.**
  SKILL.md lines 89–94 + 228. Agent can modify benchmark files, tests,
  `[profile.bench]` sections, or CI workflows and have those changes
  improve `compare_runs` deltas. This is structurally Goodhart-shaped:
  the proxy (compare_runs delta) and the target (production-code
  performance) can be decoupled via permitted edits.
  Mitigation: explicit production-code-only rule + diff-layer gate
  (implemented in `scripts/variance_gate.py` as D1a + D1b).

- **A2 — The `.take(n)` → `[..n]` example is not semantically
  equivalent.** SKILL.md lines 151–153. The recommended transformation
  is saturating on the left and panicking on the right. An agent
  pattern-matching the example can introduce runtime panics in
  production. Mitigation: replace the example or add a bounds-check
  caveat.

- **A3 — Diminishing-return threshold should scale with measurement
  variance.** SKILL.md line 233. A 1-2% threshold is at or below
  CodSpeed's own advertised Walltime variance (0.56% on Macro Runners
  per changelog, higher on standard CI). Mitigation: make the
  threshold mode-aware; require the agent to report a confidence
  qualifier when a claimed gain is below ~2× the mode's variance.

- **A4 — Hardware-state contamination is not addressed (hypothesis
  only).** No SKILL.md citation; omission is the finding. Thermal
  state, frequency scaling, and co-tenant noise can drive several
  percentage points of drift on Walltime over a 20-minute agent
  session. Empirical verification requires intra-run variance data
  the Atlas does not contain. Mitigation proposal: warm-up and
  hardware-hygiene discipline before baseline.

Each finding was written, submitted for adversarial review (founder
POV + ML-eval POV), revised in two passes, and fact-checked against
the exact SKILL.md revision.

---

## §4. Variance-gate prototype

`scripts/variance_gate.py` — 377 lines, zero external dependencies
(stdlib only), five detectors:

- **D1a forbidden-path-modification** (BLOCK): literal path gate on
  `bench/`, `conftest.py`, `build.rs`, `rust-toolchain.toml`,
  `codspeed.yml`, `.github/workflows/`, `.gitlab-ci.yml`, Go
  `*_test.go`, and CodSpeed integration directories (`cargo-codspeed/`,
  `pytest-codspeed/`, `codspeed-node/`) plus specific plugin-config
  filename patterns (`(pytest[_-])?codspeed[_-](plugin|config|cli|
  fixtures?|hooks?).{py,rs,js,ts,toml,yaml}`). Deliberately narrower
  than "any file with `codspeed` in the name" to avoid false-positives
  on production code in CodSpeed's own language-adapter repositories.
- **D1b hunk-scoped-bench-config** (BLOCK): only fires on Cargo.toml /
  pyproject.toml diffs that touch bench-related TOML sections
  (`[profile.bench]`, `[[bench]]`) or codspeed-specific tokens
  (`criterion =`, `divan =`, `pytest-codspeed`, etc.). Passes cleanly
  on dep bumps including libraries with "bench" in their name.
- **D2 test-skip-introduction** (BLOCK): regex set for `#[ignore]`,
  `pytest.mark.skip`, `t.Skip(`, commented-out `#[bench]` / `Benchmark`
  functions, `xdescribe`, `xit`.
- **D3 magnitude-asymmetry** (REVIEW_REQUIRED): fires when
  `overall_pct ≤ -20%` regardless of test-pass status.
- **D4 noise-floor-gate** (REVIEW_REQUIRED): symmetric — fires on
  either a claimed improvement or a claimed regression below the
  mode-specific variance floor (1% Sim / 1.5% WT-Macro / 3%
  WT-standard).

Test harness in `scripts/test_variance_gate.sh` — 9 scenarios, all
green:

| # | Scenario | Verdict | Detector |
|---|---|---|---|
| A | clean production change, +7.5% Sim | PASS | — |
| B | bench iteration count reduced 10000→100 | BLOCK | D1a |
| C | `#[ignore]` added to slow test | BLOCK | D2 |
| D | -35% regression in production code | REVIEW | D3 |
| E | +1.2% claimed on standard WT runner | REVIEW | D4 |
| F | empty diff | ERROR | refuses silent PASS |
| G | Cargo.toml dep bump | PASS | — (D1b correctly skips) |
| H | Cargo.toml `[profile.bench]` edit | BLOCK | D1b |
| I | -0.8% claimed on standard WT runner | REVIEW | D4 (symmetric) |

The script is documented with a LIMITATIONS section naming five known
architectural gaps (agent-supplied result trust, path-regex bypass,
D2 false-positive modes, D3 subset-trust, fragile diff parsing).
These are disclosed rather than hidden.

---

## §5. RLVR methodology companion

`rlvr_mapping.pdf` (1-page A4, 747 words) maps reward-engineering
principles from the public RLVR literature (DeepSeek-R1-Zero,
specification-gaming research, open-source training frameworks like
TRLX and verl) to the `codspeed-optimize` reward-hacking surface.
The mapping is methodological (gate design), not training (no
fine-tuning proposal). Each row of the mapping table cites a specific
finding from §3 as anchor.

Read the PDF for the mapping table and the proposed 30-day prototype
plan. In short: define a bucketed reward schema (MERGE_READY /
NEEDS_REVIEW / REJECT), compose the existing `variance_gate.py`
detectors with two new checks (bench-subset + cross-bench regression),
ship as `codspeed-optimize --strict` opt-in.

---

## §6. Linear-ready tickets

Three tickets structured for direct paste into Linear / Jira. Each
has priority, labels, context with repo-link evidence, acceptance
criteria, and an estimate.

### TICKET 1 — [agent-eval] Constrain optimization scope to production code

**Priority**: P1
**Labels**: agent-layer, codspeed-optimize, eval, reward-hacking

**Context.** `findings.md` A1 documents that the current SKILL.md
permits agent modifications to benchmark files, test suppressions,
`[profile.bench]` sections, and CI workflows. Any such modification
that improves `compare_runs` metrics without touching production code
is reward hacking by construction. No SKILL.md rule currently rejects
agent modifications to bench files or `[profile.bench]` sections. A
prototype diff-layer gate (`scripts/variance_gate.py` D1a + D1b)
catches the specific cases labelled B, C, and H in
`scripts/test_variance_gate.sh` (bench iteration count reduction,
`#[ignore]` injection, `[profile.bench]` opt-level edit).

**Acceptance criteria.**
1. SKILL.md adds a new top-level constraint before step 1:
   "Optimization targets production code only." followed by the
   specific excluded glob set.
2. A pre-commit-style gate in the codspeed-optimize plugin routes
   every proposed diff through D1a + D1b; if either fires, the
   agent's output is blocked and the user is surfaced the specific
   file list.
3. Regression test (in CodSpeed's own CI): the 9 scenarios in
   `scripts/test_variance_gate.sh` all pass against the published
   plugin's implementation.

**Estimate.** 3 person-days for SKILL.md + gate integration; 1
person-day for test-suite wiring.

**Linked evidence.**
- `findings.md` §A1
- `scripts/variance_gate.py` (D1a + D1b implementation)
- `scripts/test_variance_gate.sh` (scenarios B, C, H)

---

### TICKET 2 — [skill-doc] Fix the `.take(n)` → `[..n]` example

**Priority**: P2
**Labels**: codspeed-optimize, skill-doc, correctness

**Context.** SKILL.md line 153 offers
`.take(n) → [..n]` as an example of a "simulation-only" overhead
pattern. The transformation is semantically non-equivalent in Rust:
`.take(n)` saturates at the iterator's length, while `[..n]` panics
if `n > slice.len()`. An agent that pattern-matches on this example
can introduce production panics in code paths where tests do not
exercise `n > len`.

**Acceptance criteria.**
1. Either (a) the example is replaced with a semantically-safe
   transformation (e.g., removing an unnecessary `.clone()` in a
   hot loop, or eliminating a `format!` on the happy path), OR (b)
   the example is kept with an explicit caveat: "safe only if the
   caller enforces `n ≤ slice.len()`; otherwise keep `.take()`."
2. SKILL.md lint check: no example transformation in the skill may
   change the set of inputs on which the function panics.

**Estimate.** 0.5 person-days.

**Linked evidence.**
- `findings.md` §A2
- Rust stdlib docs for `Iterator::take` vs slice indexing

---

### TICKET 3 — [agent-eval] Bucketed reward schema + measurement-subset check

**Priority**: P1
**Labels**: agent-layer, codspeed-optimize, eval, reward-engineering

**Context.** The current `compare_runs`-delta signal is a raw scalar
that the agent can both influence (via findings A1, A2) and be misled
by (via A3, A4 — below-noise-floor gains). `rlvr_mapping.pdf`
proposes a bucketed replacement — MERGE_READY / NEEDS_REVIEW /
REJECT — each defined by a deterministic verifier. This ticket
implements the verifier pipeline.

**Acceptance criteria.**
1. A typed JSON reward contract with the three buckets and their
   deterministic checks.
2. The verifier composes three components:
   a. `variance_gate.py` (existing) — diff-layer gates D1a/D1b/D2,
      magnitude gate D3, noise-floor gate D4.
   b. Bench-subset cross-check — verify that the benches measured in
      the agent's `codspeed run` invocation match the benches
      declared in `Cargo.toml [[bench]]` / `pytest --collect-only` /
      equivalent. Agent must not silently measure a subset.
   c. Cross-bench regression detector — full-suite run after the
      proposed change; REJECT if any non-targeted bench regresses
      above threshold.
3. Shipped as an opt-in flag `codspeed-optimize --strict`; unstrict
   path remains live for backward compatibility.
4. Replay the 65-case Atlas through the verifier. Hand-label a
   stratified subsample **large enough** to give the MERGE_READY
   precision claim a useful CI — 15 cases is a starting point that
   yields a Wilson CI of roughly ±25pp at 80% precision, so the
   production target should be calibrated after Week 3's initial run
   (e.g., expand to n=40-50 if the CI is too wide). The Week-3
   deliverable is the labeled subsample + its observed precision
   with CI, not a hit-threshold claim.

**Estimate.** Epic-sized; split into three tickets during sprint
planning — (3a) reward schema + JSON contract, (3b) verifier pipeline
composition, (3c) Atlas replay + hand-labeled precision study. Total
~4 engineer-weeks. See the 30-day plan in `rlvr_mapping.pdf`.

**Linked evidence.**
- `findings.md` §A3, §A4
- `rlvr_mapping.pdf` (bucketed schema + 30-day plan)
- `scripts/variance_gate.py` LIMITATIONS §4 (subset-trust gap)
- `labeled/diagnostic_set.jsonl` (Atlas replay set)

---

## Reproducibility

Every number and figure in this report is derivable from the public
data in this repository.

```bash
# Re-run statistical analysis
python3 scripts/analyze.py

# Re-generate figures
python3 scripts/make_figures.py

# Re-run variance-gate test scenarios
scripts/test_variance_gate.sh
```

All crawl scripts accept a `GITHUB_TOKEN` via `~/.github_pat`; see
`scripts/crawl.py` for rate-limit handling.

Source of all PR comments: public GitHub via the REST API's
`search/issues?q=commenter:codspeed-hq[bot]` endpoint.

Source of all SKILL.md citations:
[github.com/CodSpeedHQ/codspeed/blob/main/skills/codspeed-optimize/SKILL.md](https://github.com/CodSpeedHQ/codspeed/blob/main/skills/codspeed-optimize/SKILL.md),
line numbers refer to the revision at HEAD as of 2026-04-18.

---

## Threat model and scope

**Threat model**: reward hacking by a **trusted** agent
(codspeed-optimize is invoked by the user, not by adversarial input).
The agent is assumed honest but under incentive pressure to produce
favorable `compare_runs` deltas. Out-of-scope: prompt injection into
flamegraph output, MCP-layer authentication boundaries, or
adversarial control of the underlying LLM.

**Scope**: SKILL.md spec audit + reward-surface audit. Not in scope:
the accuracy of CodSpeed's internal Valgrind/Callgrind implementation,
the MCP server's transport-layer security, or the efficacy of
codspeed-optimize as a whole in real user sessions (a controlled
A/B trial against a baseline agent would be required for that claim).

**Data provenance**: all bot comments were retrieved via the
unauthenticated portion of the GitHub REST API; all repo content
cited is public. No private or paid-tier CodSpeed data was used.

---

## Non-goals of this audit

- I did not run `codspeed-optimize` end-to-end; auth + Macro Runner
  access are enterprise-only. The audit is static (spec + diff
  analysis + statistical inference on bot output), not dynamic.
- I did not retrain or fine-tune any LLM. The RLVR companion operates
  at the gate layer, not the training layer.
- I did not propose changes to CodSpeed's measurement core (Valgrind
  integration, cache-sim accuracy, Macro Runner hardware spec).

Out of scope.

---

## Companion artifacts

| Artifact | What it is |
|---|---|
| `findings.md` | Full four-finding SKILL.md audit (1,759 words) |
| `rlvr_mapping.pdf` | 1-page RLVR methodology companion (747 words) |
| `scripts/variance_gate.py` | 5-detector prototype, 377 lines, stdlib-only |
| `scripts/test_variance_gate.sh` | 9-scenario smoke test, all green |
| `scripts/analyze.py` | W v2 statistical analysis |
| `scripts/make_figures.py` | Figure generator |
| `labeled/diagnostic_set.jsonl` | 65-case Atlas |
| `labeled/analysis_results.json` | Structured stats |
| `labeled/summary.md` | Human-readable Atlas table |
| `figures/` | 3 PNGs referenced above |
| `raw/` | 13,490 PR metadata (full crawl) |
| `parsed/` | 1,242 enriched PRs |

---

*Prepared in April 2026. All analysis is reproducible from this
repository; all critiques are bounded by the non-goals above. Data
provenance is 100% public (GitHub REST API). This is an independent
study of CI performance-regression workflows; no affiliation with
CodSpeed or any customer repository surveyed.*
