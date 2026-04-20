# SKILL.md audit: four failure modes in `codspeed-optimize`

> **This is §3 of the CI Regression Atlas** — see `REPORT.md` for the
> full context. The atlas's primary reading path is the three empirical
> findings in `REPORT.md`'s TL;DR (outcome variance, benchmark
> recurrence, response latency, drawn from 13,490 bot comments). This
> file is the supplementary safety-audit layer, a spec review of the
> `codspeed-optimize` SKILL.md for any future agent-driven optimizer.
> Audit findings here are labelled **A1–A4** to distinguish them from
> the empirical-finding numbering (Finding 1, 2, 3) in REPORT.md.

This section audits the `codspeed-optimize` skill prompt shipped by CodSpeed
on 2026-03-16 as part of their Claude Code plugin. The audit is static: it
analyzes the prompt as a *specification* for an adversarial optimizer,
identifies gaps, and proposes specific mitigations.

Each finding includes:
- The exact SKILL.md citation (or the absence of one)
- The gap in the specification
- A hypothetical failure scenario
- A proposed mitigation

All findings are against `skills/codspeed-optimize/SKILL.md` at commit
[`HEAD`](https://github.com/CodSpeedHQ/codspeed/blob/main/skills/codspeed-optimize/SKILL.md)
as of 2026-04-18. Line numbers refer to that revision.

---

## A1. Optimization scope is not constrained to production code

**SKILL.md citation (lines 89–94):**

> "**Important constraints:**
>  - Only change code you've read and understood
>  - Preserve correctness — run existing tests after each change
>  - Keep changes minimal and focused
>  - Don't over-engineer — the simplest fix that works is the best fix"

**SKILL.md citation (line 228):**

> "**Everything goes through CodSpeed.** Never run benchmarks outside of the
> CodSpeed CLI. [...] The CodSpeed MCP tools (`compare_runs`,
> `query_flamegraph`, `list_runs`) are your source of truth — use them to
> read results, not terminal output."

**Gap.** The specification imposes two constraints on the agent's changes:
*read-before-modify* and *tests-pass*. Neither forbids the agent from
modifying the benchmark files themselves, the test suite, the CodSpeed
configuration (`codspeed.yml`), the cargo-codspeed build flags, or the CI
workflow. The agent's reward signal is `compare_runs` output — the same
output the agent can directly affect by modifying what gets measured.

**Hypothetical failure scenario.** `codspeed-optimize` is pointed at a hot
function that resists algorithmic improvement. The agent has read it and
understood it; no obvious win exists. Under the incentive to keep iterating
until the flamegraph is flat (step 7), the agent reduces the benchmark's
inner-loop iteration count from 10,000 to 100, or swaps a representative
input file for a trivial one, or adds `#[ignore]` to a slow test that is
causing timeouts. `compare_runs` reports a large improvement. All existing
tests pass. The agent declares success.

This is the Goodhart pattern: when the measurement becomes the target,
modifying the measurement is easier than modifying the thing being measured.

**Proposed mitigation.** Add an explicit, literal rule to SKILL.md before
step 1:

> **Optimization targets production code only.** Never modify files or
> sections under: `bench/`, `benches/`, `tests/bench_*`, `benchmarks/`,
> `perf/`, any `*_test.go` file that defines `Benchmark*` functions,
> `codspeed.yml`, the `[profile.bench]` section of `Cargo.toml`, any
> `pyproject.toml` section that configures pytest-benchmark or
> pytest-codspeed, `.github/workflows/` (or any other CI config that
> invokes CodSpeed), or any file whose name contains `bench` or
> `codspeed`. If measurement infrastructure appears to need changes,
> stop and ask the user. A change that improves `compare_runs` metrics
> without touching production code is by default reward hacking, not
> optimization.

A supporting check can be implemented as a pre-commit gate: compute the
diff's touched files and touched TOML/YAML sections; if any match the
excluded set, block the agent output.

**Scope of this gate.** The glob list above is a speed bump, not a wall.
It catches the obvious cases but a determined code-modifying agent can
route around it (e.g., by introducing a new bench harness at a novel
path, or by rewriting `#[bench]` attribute inputs via a macro). The
literal rule encoded in SKILL.md is the primary defense; the file-gate
is a secondary check, not a substitute.

---

## A2. The `.take(n)` → `[..n]` example is not semantically equivalent

**SKILL.md citation (lines 151–153):**

> "**Patterns that often show up in simulation but NOT walltime:**
>  - Iterator adapter overhead (e.g., `.take(n)` to `[..n]`) — branch
>    prediction hides it"

**Gap.** The two transformations are semantically different in Rust.
`iter.take(n)` saturates: if the iterator yields fewer than `n` items, it
yields all of them. `slice[..n]` panics if `n > slice.len()`. An agent that
pattern-matches on this example in SKILL.md will replicate the
transformation in real code where the saturating behavior was load-bearing.

**Hypothetical failure scenario.** The agent encounters
`some_slice.iter().take(user_supplied_n)` in a hot path. It applies the
transformation from SKILL.md's example, producing
`some_slice[..user_supplied_n].iter()`. All 200 existing tests pass because
every test calls the function with `n ≤ len`. The change ships. A month
later, a user supplies input where `n > len` and the service panics. Root
cause is traced to the transformation guided by the CodSpeed skill.

**Proposed mitigation.** Either:

(a) Replace the example with a transformation that is genuinely safe in
both senses, e.g. removing an unnecessary `.clone()` in a hot loop, or
eliminating a `format!` call that fires on the happy path.

(b) Keep the example but add an explicit caveat:

> *Note: this transformation is only safe if the caller enforces
> `n ≤ slice.len()`. If the bound is not enforced elsewhere, keep
> `.take()`.*

This finding is narrow but concrete and cheap to fix.

---

## A3. Diminishing-return threshold should scale with measurement variance

**SKILL.md citation (line 233):**

> "**Know when to stop.** Diminishing returns are real. When gains drop
> below 1-2%, you're usually done unless the user has a specific target."

(The same 1-2% figure also appears at line 175: "You've hit diminishing
returns (<1-2% improvement per change)".)

**Cross-reference (codspeed.io, accessed 2026-04-18):** CodSpeed markets
"variance under 1%" for Instrumentation mode and measured 0.56% variance
on its Macro Runners for Walltime mode.

**Gap.** The "1–2%" threshold in SKILL.md is mode-agnostic. For
Instrumentation mode, 1% is reasonable because the mode is deterministic
simulation and variance is near-zero. For Walltime mode on standard CI
runners, 1–2% is at or below the documented noise floor — a single 1.5%
"improvement" may not be statistically distinguishable from measurement
variance.

**Hypothetical failure scenario.** The agent runs codspeed-optimize in
Walltime mode on a standard GitHub Actions runner. It reports three
sequential "improvements" of 1.5%, 1.8%, and 1.2%. The user accepts the
changes. In fact, within-run variance on that runner is ±2%, so the
individual deltas are indistinguishable from noise. Added code complexity
ships for no real gain.

**Proposed mitigation.** Tie the threshold to the measurement mode and the
observed variance of the specific run:

- Instrumentation mode: retain 1% threshold (simulation is deterministic)
- Walltime mode on Macro Runners: raise threshold to ≥1.5% (≈ 3× the
  advertised 0.56% average variance)
- Walltime mode on non-Macro runners: require the agent to compute
  observed variance from the last *N* runs of the same benchmark and
  set threshold to ≥3× observed σ, with a minimum of 3%

Additionally: require the agent to report, alongside each claimed
improvement, either (a) the confidence interval on the rate difference,
or (b) a qualitative flag if the improvement is smaller than ~2× the
mode's variance.

---

## A4. Hardware-state contamination is not addressed (hypothesis-only)

**SKILL.md citation:** None directly. This finding concerns guidance that
is absent from the specification.

**Important caveat: this finding is a reasoned hypothesis based on general
performance-engineering knowledge, not an empirical result from this
audit.** The Atlas dataset does not contain the intra-run variance data
required to demonstrate this failure mode directly.

**Gap.** SKILL.md walks the agent through a measurement loop — establish
baseline, iterate, compare — with zero guidance on the physical state of
the machine the benchmarks run on. Specifically absent: CPU frequency
scaling, thermal state, warm-up protocol, co-tenant noise on self-hosted
runners, I/O cache state. These factors can drive several percentage
points of measurement drift on Walltime mode over a typical 20-minute
agent session.

**Hypothetical failure scenario.** The agent runs in Walltime mode on a
self-hosted CI runner shared with other jobs. Over 20 minutes it runs the
same benchmark 6 times. The runner's CPU transitions from cold to thermal
throttling, and a co-tenant job briefly saturates memory bandwidth in the
middle. Under these conditions, several percentage points of drift in wall-clock
times across the session from hardware state alone is within the
expected range for shared CI infrastructure without explicit
frequency-governor or thermal controls (a point standard
benchmark-library documentation like Google Benchmark's
`--benchmark_repetitions` flag and the broader performance-engineering
literature has been making for years). The agent could then attribute
such drift to its own code changes and make iterative "improvements"
that are in fact tracking hardware-state changes.

**Proposed mitigation.** Before step 1, add:

> **Warm-up and hardware-state hygiene (Walltime only):**
> 1. Before establishing a baseline, run the benchmark suite twice in
>    discard mode to warm caches and stabilize frequency scaling.
> 2. If the measurement environment is not a CodSpeed Macro Runner, warn
>    the user that Walltime results may drift several percentage points
>    from hardware state alone, and recommend Instrumentation mode for
>    iteration (reserving Walltime for final validation).
> 3. Leave enough idle time between measured runs for thermal recovery;
>    the exact interval should be calibrated to the host (sustained
>    vector workloads on higher-TDP parts need longer intervals than
>    single-core integer benchmarks on low-power parts, so a fixed
>    default is suboptimal; surface this as a configuration knob).
> 4. If multiple baseline runs show unusually high intra-baseline
>    variance (a configurable threshold, e.g., 2× the mode's advertised
>    variance as a starting point pending calibration), stop and surface
>    this to the user rather than proceeding to optimize.

Alternatively, encode these as configuration defaults in `cargo-codspeed`
and Python/Node/Go runners, so the skill does not have to reason about
hardware hygiene at all.

---

## Summary

Of the four findings:

- **A1 (bench/instrument scope)** and **A4 (hardware state)** are
  proposed-as-team-roadmap-level additions to SKILL.md. A1 closes the
  reward-hacking loophole; A4 closes the measurement-reliability
  loophole. Both can be implemented as additional sections in SKILL.md
  plus a lightweight pre-commit check.

- **A2 (`.take` panic)** is a narrow, concrete, cheap fix: either change
  the example or add one sentence of caveat.

- **A3 (threshold scaling)** is a refinement of existing guidance rather
  than a new rule. The current 1–2% number is not wrong so much as
  insufficiently specified.

**Not included in this audit** (scope limit): runtime observability of
the agent's reasoning (trace logging), auth and permission boundaries of
the MCP server, and adversarial prompt injection into flamegraph output.
The threat model here is **reward hacking by a trusted agent**
(codspeed-optimize is invoked by the user, not by adversarial inputs),
not injection or escalation attacks. Injection and auth-boundary
concerns are real but belong to a separate review pass on the MCP
protocol layer, not on the skill spec itself.

**Reproducibility.** SKILL.md is public at the URL cited above. Every
citation in this document is recoverable by line number against that
revision. The hypothetical scenarios are constructed from the absence or
looseness of specific lines, not from runtime observation of the agent.
