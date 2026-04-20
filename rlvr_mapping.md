# Verifiable Rewards for Code-Modifying Performance Agents

A methodology note mapping RLVR (Reinforcement Learning with Verifiable
Rewards) Training Gym principles to the reward-hacking surface of
CodSpeed's `codspeed-optimize` skill. Companion to `findings.md` (SKILL.md
audit) and `scripts/variance_gate.py` (prototype mitigation).

## The problem

`codspeed-optimize` rewards the agent on `compare_runs` deltas. The audit
in `findings.md` identifies five ways an agent can produce a favorable
delta without a real improvement: modifying benchmark files (A1 / D1a),
editing bench TOML sections (A1 / D1b), suppressing tests (A1 / D2),
chasing noise-floor gains (A3 / D4), and shipping large regressions that
pass existing tests (A1 / D3). This is the standard reward-hacking
problem, and RLVR literature offers a direct treatment.

## Mapping

| Principle (source) | Why it matters | codspeed-optimize application | Anchor |
|---|---|---|---|
| **Verifiable reward** — the reward is a mechanical check, not an LLM judgment (RLVR core claim). | Prevents the agent from optimizing the judge's flattery-sensitivity. | `compare_runs` is already mechanical. The gap is that it is *trusted without verification*: the delta is accepted regardless of what the agent changed. Insert a verification gate (change-scope + measurement-integrity) before trusting the delta. | A1 (audit) |
| **Eval-boundary isolation** — evaluation runs in a context the agent cannot modify (standard ML ops, pre-RLVR). | Prevents state leakage from action to reward computation; prevents the agent from editing the rubric. | Hard diff-layer boundary: agent may propose production-code changes; may not modify `codspeed.yml`, cargo-codspeed flags, bench definitions, CI workflows, or `[profile.bench]` sections. `variance_gate.py`'s D1a + D1b implement this at the diff layer. | A1, A1-scope gate |
| **Discrete bucketed reward over raw scalar** — a small reward gradient on a noisy signal can be hill-climbed in the wrong direction. | A 2% "improvement" below the mode's variance floor is indistinguishable from noise; rewarding it encourages phantom wins. | Bucket deltas into three verifier-gated classes instead of treating the raw % as a reward: **MERGE_READY** (production-only, magnitude ≥ 2× mode variance, full-suite pass), **NEEDS_REVIEW** (ambiguity on any above), **REJECT** (bench-infra touched, tests suppressed, below noise floor, or regression magnitude ≥ 20%). | A3 (threshold), A4 (hardware noise) |
| **Cross-task robustness** — rewards on a single targeted task invite overfit. | Targeted optimization can silently regress adjacent code paths. | Every acceptance must also pass the **full** bench suite, not just the targeted subset. If non-targeted benches regress, require REVIEW. This is the "measurement-subset" gap documented in `variance_gate.py`'s LIMITATIONS section (item #4). | Gate gap (not in A1-A4) |
| **No reward-shape that reverses the objective** — the agent optimizes exactly what you reward. | Rewarding "tests-pass + improved metric" lets the agent weaken the tests. | Enforce finding A1 literally: optimization targets production code only. A change that improves `compare_runs` without touching production code is by construction reward hacking, not optimization. `findings.md` A2 (unsafe `.take(n)` example) also lives in this failure class. | A1, A2 |

---

## 30-day prototype plan

**Week 1 — Define the bucketed reward schema.** Three buckets, each with
a deterministic verifier. The verifier composes (a) the variance_gate
detectors, (b) a bench-subset cross-check, and (c) a cross-bench
regression detector. Ship the schema as a typed JSON contract.

**Week 2 — Build verifier pipeline.** Compose `variance_gate` + bench-subset
cross-check + cross-bench regression detector into `codspeed-optimize
--strict`. Keep the unstrict path live for backward compat.

**Week 3 — Replay Atlas + hand-label subsample.** Run verifier over the
65-case Atlas; report bucket distribution. Because the Atlas is
observational, the distribution alone is descriptive. Hand-label a
stratified subsample (~15 cases, 5 per bucket) against "was this a real
improvement" to measure verifier precision/recall. The labeled subsample
is the real Week-3 deliverable.

**Week 4 — Ship as opt-in.** Release `--strict`. Instrument
reward-bucket telemetry.

## Scope note

This proposal operates at the **reward/gate layer**, not the underlying
LLM. No retraining.

The broader RLVR literature (DeepSeek-R1-Zero's verifiable-reward
pretraining, Anthropic's work on specification gaming, the TRLX /
verl open-source training frameworks) converges on one principle:
reward *verifiability* — whether the reward signal can be
mechanically checked against ground truth — matters more than reward
*shape* (binary vs continuous, dense vs sparse). The implication for
`codspeed-optimize`: invest in gate verifiability (diff-layer
boundary + bench-subset cross-check) before tuning the delta-scoring
curve.

## References

`findings.md` (SKILL.md audit) · `scripts/variance_gate.py` (5 detectors,
9 tests) · `scripts/analyze.py` (W v2 analysis) · RLVR public literature:
DeepSeek-R1-Zero (arXiv 2501.12948) for verifiable-reward pretraining,
TRLX / verl open-source frameworks · CodSpeed marketing: "<1% variance"
Instrumentation, "0.56% average variance" Walltime on Macro Runners.
