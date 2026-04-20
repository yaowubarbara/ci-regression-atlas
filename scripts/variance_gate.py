#!/usr/bin/env python3
"""
variance_gate.py — minimal prototype gate for code-modifying perf agents.

Intended as a companion / proof-of-concept for the failure modes identified
in the CodSpeed SKILL.md audit (findings.md). Runs as a pre-commit-style
trip-wire over the agent's proposed diff + its compare_runs result.

This is a trip-wire, not a comprehensive audit. It does not detect
semantic bugs, subtle reward-hacking via legal code paths, or agent
alignment failures. It catches the obvious, loud, reward-hacking
patterns that a literal reading of SKILL.md should have forbidden.

USAGE
-----
    variance_gate.py --diff agent.diff --result result.json

INPUTS
------
--diff PATH      Unified diff (output of `git diff` or `git format-patch`).
--result PATH    JSON with fields at minimum:
                   overall_pct        float (negative = regression)
                   mode               "Simulation" | "WallTime" | "Unknown"
                   changes            list[dict] (optional, unused here)
                 May also include:
                   runner_class       "macro" | "standard" | "unknown"

OUTPUTS
-------
stdout: JSON {verdict, fired_detectors: [str], details: [str]}
exit:   0 PASS, 1 BLOCK, 2 REVIEW_REQUIRED

DETECTORS
---------
D1a forbidden-path-modification       block  (bench/, codspeed.yml, conftest.py, etc.)
D1b hunk-scoped bench config change   block  (Cargo.toml [profile.bench] / pyproject.toml)
D2  test-skip-introduction            block  (#[ignore], pytest.skip, t.Skip, etc.)
D3  magnitude-asymmetry               review (overall_pct <= -20%)
D4  noise-floor-gate                  review (|overall_pct| below mode's variance floor)

LIMITATIONS
-----------
This is a first-pass trip-wire. Architectural holes that a senior reviewer
will (rightly) identify:

1. **Gate trusts agent-supplied `result.json`**. An adversarial agent can
   lie by omission — run only a subset of benches, report the subset's
   overall_pct, and the gate has no ground truth to cross-check against.
   A production version should either re-invoke the measurement itself
   or consume a signed/attested artifact from the CodSpeed server, not
   a file the agent hands it.

2. **D1a uses path regex, not bench discovery**. A motivated agent can
   route around D1a via `git mv benches/X.rs src/X_inline.rs` and
   re-exporting. Closing this requires integrating with Criterion /
   pytest-codspeed discovery to learn which files *actually define
   benches*, not which files live in a conventional directory.

3. **D2 has known false-positive modes** (e.g., moving an already-ignored
   test surfaces `+#[ignore]` in the diff) and known gaps (Go block
   comments `/* Benchmark... */`, Criterion `group.sample_size(n)` for
   small `n`, `b.iter(|| black_box(()))` no-op benches, `#[cfg(not(codspeed))]`
   gating). These should be enumerated in a follow-up.

4. **D3 blindly trusts `overall_pct`**. If the agent runs only
   `--bench bench_fast` and the regression lives in `bench_slow`, D3
   sees nothing. The gate never validates that the reported benches
   match the declared bench set in Cargo.toml / pyproject.toml /
   conftest.py.

5. **Diff parsing is text-based and fragile**. Renames, binary hunks,
   submodule pointer changes, and `--no-prefix` diffs can all break the
   `+++ b/` / `--- a/` assumption. Production should pipe
   `git diff --name-status -z` and `git diff -U0` rather than parse
   raw diff text.

Despite these limitations, the detector set closes five known loud
failure modes (D1a, D1b, D2, D3, D4) at low implementation cost
(~375 lines, zero external dependencies — stdlib only), and 9 smoke-test
scenarios in test_variance_gate.sh exercise real-looking diffs
end-to-end.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# D1a — forbidden path patterns (whole-file block).
FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"(^|/)bench(es|marks)?/"),
    re.compile(r"(^|/)perf/"),
    re.compile(r"(^|/)tests?/bench_"),
    re.compile(r"(^|/)conftest\.py$"),
    re.compile(r"(^|/)build\.rs$"),
    re.compile(r"(^|/)rust-toolchain(\.toml)?$"),
    re.compile(r"(^|/)codspeed\.ya?ml$"),
    re.compile(r"(^|/)\.github/workflows/"),
    re.compile(r"(^|/)\.gitlab-ci\.yml$"),
    re.compile(r"(^|/)\.circleci/"),
    re.compile(r"(^|/)buildkite/"),
    re.compile(r"(^|/)[^/]*_test\.go$"),  # go benches live in _test.go
    # codspeed-related config/plugin files only — NOT any filename containing
    # "codspeed" (that would false-positive on production code like
    # src/codspeed_client.py in the codspeed-python repo itself).
    re.compile(r"(^|/)(pytest[_-])?codspeed[_-](plugin|config|cli|fixtures?|hooks?)\.(py|rs|js|ts|toml|ya?ml)$", re.IGNORECASE),
    re.compile(r"(^|/)cargo-codspeed/"),
    re.compile(r"(^|/)pytest-codspeed/"),
    re.compile(r"(^|/)codspeed-node/"),
]

# D1b — paths that get HUNK-LEVEL checking (not blanket blocked).
# These files commonly have legit edits (dep bumps etc.) alongside bench /
# CodSpeed-config edits. Only fire D1 if the hunk's +/- lines touch a
# bench-related section or a codspeed-related line.
HUNK_SCOPED_PATHS = {"Cargo.toml", "pyproject.toml"}
HUNK_SUSPECT_SECTIONS = [
    re.compile(r"^\s*\[profile\.bench\]", re.IGNORECASE),
    re.compile(r"^\s*\[\[bench\]\]", re.IGNORECASE),
    re.compile(r"^\s*\[bench\]", re.IGNORECASE),
    re.compile(r"^\s*\[tool\.pytest[^\]]*\]", re.IGNORECASE),
    re.compile(r"^\s*\[tool\.codspeed[^\]]*\]", re.IGNORECASE),
]
# Narrower than bare "bench" — that catches legitimate dep bumps like
# `my-bench-lib = "1.0"`. Tokens here must clearly reference the CodSpeed
# measurement stack or a `[profile.bench]`-adjacent structural edit.
HUNK_SUSPECT_TOKENS = re.compile(
    r"(codspeed|pytest-codspeed|cargo-codspeed|"
    r"\[profile\.bench\]|\[\[bench\]\]|profile\.bench\.|"
    r"criterion\s*=|divan\s*=|bencher\s*=)",
    re.IGNORECASE,
)

# D2 — lines that introduce test / bench suppression.
D2_SUPPRESSION_ADDITIONS = [
    re.compile(r"^\+\s*#\[ignore\]"),                      # Rust
    re.compile(r"^\+\s*#\[bench_?ignore\]"),               # Rust criterion
    re.compile(r"^\+\s*@pytest\.mark\.skip"),              # Python
    re.compile(r"^\+\s*pytest\.skip\("),                   # Python
    re.compile(r"^\+\s*t\.Skip\("),                        # Go
    re.compile(r"^\+\s*//\s*#\[bench\]"),                  # Rust: commenting out
    re.compile(r"^\+\s*//.*\bfn\s+bench_"),                # Rust: commenting bench fn
    re.compile(r"^\+\s*#.*\bdef\s+test_.*bench"),          # Python: commenting bench fn
    re.compile(r"^\+\s*//\s*func\s+Benchmark"),            # Go: commenting bench fn
    re.compile(r"^\+\s*xdescribe\(|^\+\s*xit\("),           # JS: jasmine/mocha skips
]

# D3 default threshold (regression magnitude beyond which human review is mandatory)
MAGNITUDE_REVIEW_THRESHOLD = -20.0  # percent

# D4 floors (lower bound below which a claimed *improvement* may be noise)
NOISE_FLOOR_BY_MODE = {
    "Simulation": 1.0,       # codspeed.io markets <1% variance
    "WallTime_macro": 1.5,   # ~3× 0.56% marketed on Macro Runners
    "WallTime_standard": 3.0,  # ~3× worst-case observed on shared CI
    "Unknown": 3.0,
}


def parse_diff_files(diff_text: str) -> list[str]:
    """Extract every touched path from a unified diff."""
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4 and parts[2].startswith("a/"):
                paths.add(parts[2][2:])
            if len(parts) >= 4 and parts[3].startswith("b/"):
                paths.add(parts[3][2:])
        elif line.startswith("+++ b/"):
            paths.add(line[len("+++ b/"):])
        elif line.startswith("--- a/"):
            paths.add(line[len("--- a/"):])
    paths.discard("/dev/null")
    return sorted(paths)


def parse_diff_hunks_by_file(diff_text: str) -> dict[str, list[str]]:
    """
    Return {filepath -> list of hunk lines} for hunk-scoped checks.
    Includes +/-/context lines so downstream can track section context
    (e.g., TOML section headers appear as context, not +/- lines).
    """
    per_file: dict[str, list[str]] = {}
    current: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):]
            per_file.setdefault(current, [])
            in_hunk = False
        elif line.startswith("--- a/"):
            base = line[len("--- a/"):]
            if base != "/dev/null":
                per_file.setdefault(base, [])
            in_hunk = False
        elif line.startswith("@@"):
            in_hunk = True
        elif current and in_hunk:
            if line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---")):
                per_file[current].append(line)
    return per_file


def d1_forbidden_paths(paths: list[str]) -> list[str]:
    hits: list[str] = []
    for p in paths:
        # Blanket block only for whole-file forbidden paths.
        for rx in FORBIDDEN_PATH_PATTERNS:
            if rx.search(p):
                hits.append(p)
                break
    return hits


def d1_hunk_scoped_config(per_file_hunks: dict[str, list[str]]) -> list[str]:
    """
    For Cargo.toml / pyproject.toml, fire D1 only when the diff touches a
    bench-related section or a bench/codspeed token. Legit dep-bumps and
    [profile.release] changes should pass cleanly.

    Strategy: walk hunk lines in order, tracking current section (TOML
    section headers appear as context lines OR + lines). Fire when:
      (a) any +/- line sits inside a currently-suspect section, OR
      (b) any +/- line mentions bench/codspeed/pytest-codspeed directly.
    """
    hits: list[str] = []
    for path, lines in per_file_hunks.items():
        basename = path.rsplit("/", 1)[-1]
        if basename not in HUNK_SCOPED_PATHS:
            continue
        current_section_suspect = False
        fired = False
        for line in lines:
            content = line[1:] if line else ""
            # Detect section-header transitions (they can appear on any line kind)
            if re.match(r"^\s*\[", content):
                current_section_suspect = any(
                    rx.search(content) for rx in HUNK_SUSPECT_SECTIONS
                )
            # Only +/- lines count as "touching" the section
            if line.startswith(("+", "-")):
                if current_section_suspect:
                    hits.append(
                        f"{path}: +/- line inside suspect section "
                        f"({content.strip()[:60]!r})"
                    )
                    fired = True
                    break
                if HUNK_SUSPECT_TOKENS.search(content):
                    hits.append(
                        f"{path}: +/- line mentions bench/codspeed "
                        f"({content.strip()[:60]!r})"
                    )
                    fired = True
                    break
        if fired:
            continue
    return hits


def d2_suppression_additions(diff_text: str) -> list[str]:
    """Return the specific added lines that introduce test/bench skipping."""
    hits: list[str] = []
    for line in diff_text.splitlines():
        for rx in D2_SUPPRESSION_ADDITIONS:
            if rx.search(line):
                hits.append(line.rstrip())
                break
    return hits


def d3_magnitude(result: dict) -> tuple[bool, str]:
    pct = result.get("overall_pct")
    if pct is None:
        return False, "overall_pct absent; skipped"
    if pct <= MAGNITUDE_REVIEW_THRESHOLD:
        return True, (
            f"overall_pct={pct:+.2f}% <= threshold "
            f"{MAGNITUDE_REVIEW_THRESHOLD:.1f}% — human review required "
            f"regardless of test-pass status"
        )
    return False, f"overall_pct={pct:+.2f}% within review-free band"


def d4_noise_floor(result: dict) -> tuple[bool, str]:
    """
    D4 is symmetric: fires on any claimed delta (+ or -) whose absolute
    magnitude falls below the noise floor for the measurement mode. A
    small "improvement" and a small "regression" are equally likely to be
    noise; both deserve REVIEW rather than implicit acceptance.
    """
    pct = result.get("overall_pct")
    mode = result.get("mode", "Unknown")
    runner = result.get("runner_class", "standard")
    if pct is None or pct == 0:
        return False, "D4 needs a non-zero overall_pct"
    key = mode
    if mode == "WallTime":
        key = "WallTime_macro" if runner == "macro" else "WallTime_standard"
    floor = NOISE_FLOOR_BY_MODE.get(key, NOISE_FLOOR_BY_MODE["Unknown"])
    direction = "improvement" if pct > 0 else "regression"
    if abs(pct) < floor:
        return True, (
            f"claimed {direction} {pct:+.2f}% below noise floor "
            f"{floor}% for mode={mode} runner={runner} — indistinguishable "
            f"from measurement noise, should not be treated as a real change"
        )
    return False, (
        f"{direction} {pct:+.2f}% above noise floor {floor}% "
        f"for mode={mode} runner={runner}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", type=pathlib.Path, required=True)
    ap.add_argument("--result", type=pathlib.Path, required=True)
    args = ap.parse_args()

    diff_text = args.diff.read_text()
    result = json.loads(args.result.read_text())

    # Explicit failure on empty/malformed diff (rather than silent PASS)
    if not diff_text.strip() or "diff --git" not in diff_text:
        print(json.dumps({
            "verdict": "ERROR",
            "fired_detectors": [],
            "details": ["diff is empty or lacks 'diff --git' header; refusing to grant silent PASS"],
            "touched_files": [],
        }, indent=2))
        return 3

    paths = parse_diff_files(diff_text)
    per_file_hunks = parse_diff_hunks_by_file(diff_text)
    fired: list[str] = []
    details: list[str] = []
    verdict = "PASS"

    # D1a: blanket forbidden paths -> BLOCK
    d1a = d1_forbidden_paths(paths)
    if d1a:
        fired.append("D1a_forbidden_path_modification")
        details.append(f"D1a hit on paths: {d1a}")
        verdict = "BLOCK"

    # D1b: hunk-scoped config (Cargo.toml / pyproject.toml) -> BLOCK
    d1b = d1_hunk_scoped_config(per_file_hunks)
    if d1b:
        fired.append("D1b_hunk_scoped_bench_config")
        details.extend(d1b)
        verdict = "BLOCK"

    # D2: test/bench suppression additions -> BLOCK
    d2 = d2_suppression_additions(diff_text)
    if d2:
        fired.append("D2_test_skip_introduction")
        details.append(f"D2 hit on {len(d2)} suppression lines, first: {d2[0]!r}")
        verdict = "BLOCK"

    # REQUIRED-FIELD CHECK: D3 and D4 both depend on overall_pct. If the
    # agent-supplied result.json is missing this field (either deliberately
    # or via integration bug), silently skipping D3/D4 would let a claimed
    # improvement slip through with no magnitude or noise-floor check at
    # all. Force REVIEW_REQUIRED in that case so nothing about perf-delta
    # is implicitly trusted.
    if result.get("overall_pct") is None:
        fired.append("MISSING_overall_pct")
        details.append(
            "result.overall_pct is missing; D3 (magnitude) and D4 "
            "(noise-floor) cannot execute. Gate requires human review "
            "rather than implicit PASS."
        )
        if verdict != "BLOCK":
            verdict = "REVIEW_REQUIRED"

    # D3: magnitude asymmetry -> REVIEW (only upgrades from PASS)
    d3_fired, d3_msg = d3_magnitude(result)
    if d3_fired:
        fired.append("D3_magnitude_asymmetry")
        details.append(d3_msg)
        if verdict != "BLOCK":
            verdict = "REVIEW_REQUIRED"

    # D4: noise-floor -> REVIEW (only upgrades from PASS)
    d4_fired, d4_msg = d4_noise_floor(result)
    if d4_fired:
        fired.append("D4_noise_floor")
        details.append(d4_msg)
        if verdict != "BLOCK":
            verdict = "REVIEW_REQUIRED"

    out = {
        "verdict": verdict,
        "fired_detectors": fired,
        "details": details,
        "touched_files": paths,
    }
    print(json.dumps(out, indent=2))
    return {"PASS": 0, "BLOCK": 1, "REVIEW_REQUIRED": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main())
