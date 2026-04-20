# CI Regression Atlas

An empirical study of how CI performance regressions are handled in
the wild, built from the public `codspeed-hq[bot]` comment history
across 11 widely-used OSS repositories.

## What this is

A 13,490-comment dataset, a 65-case hand-labelled diagnostic set, and
three descriptive findings about flag-to-outcome behavior in real
engineering workflows. Plus a supplementary static audit of the
`codspeed-optimize` Claude Code skill specification and a 377-line
prototype gate (`variance_gate.py`) closing the reward-hacking
surfaces the audit identifies.

Start with [`REPORT.md`](./REPORT.md) for the full analysis, or
[`REPORT_summary.md`](./REPORT_summary.md) for a 3-page executive
summary.

## Scope

- **In scope**: descriptive statistics on outcome distributions,
  benchmark recurrence, and response-time patterns in public bot
  comments; static audit of the open SKILL.md specification.
- **Out of scope**: dynamic replay of `codspeed-optimize`, accuracy
  of CodSpeed's Valgrind/Callgrind implementation, privacy
  boundaries of the MCP server.

## Dataset

| Source | Content |
|---|---|
| GitHub REST API | 13,490 `codspeed-hq[bot]` comments across 11 repos |
| Enriched subset | 1,242 PRs with parsed bot output + PR outcome |
| Diagnostic set | 65 cases hand-labelled by language / magnitude / outcome |

The 11 repositories surveyed: `astral-sh/ruff`, `astral-sh/uv`,
`biomejs/biome`, `fastapi/fastapi`, `langchain-ai/langchain`,
`pydantic/pydantic`, `pydantic/pydantic-core`, `tursodatabase/turso`,
`vercel/next.js`, `vercel/turborepo`, `withastro/astro`.

All comments are public. No private or paid-tier data was used.

## Findings at a glance

1. **Merge-after-flag rate varies by ~40 percentage points across
   reportable-sized customers** (pydantic 62%, ruff 22%; both n ≥ 8).
   An order of magnitude larger than the within-repo Simulation vs
   WallTime mode effect.
2. **Five specific benchmarks account for 45% of the 65-case
   diagnostic set**, concentrated in langchain, pydantic, next.js,
   and ruff. (Selection-bias caveat: the sampler favors
   single-benchmark PRs, so fleet-wide concentration is likely
   lower.)
3. **Response latency is bimodal**: 19% of flagged PRs merge within
   15 minutes of the bot comment, the rest distributed from hours to
   days.

See [`REPORT.md`](./REPORT.md) §TL;DR for the full tables and
[`REPORT_summary.md`](./REPORT_summary.md) for a compact read.

## Reproducibility

All code is zero-dependency stdlib Python (one optional `matplotlib`
dep for figures). To reproduce from scratch:

```bash
# Requires a GitHub PAT in ~/.github_pat for the crawl
python3 scripts/crawl.py          # Phase A: ~30 min, rate-limited
python3 scripts/enrich.py         # Phase B: ~1 hour
python3 scripts/pick.py           # Phase C: diagnostic set
python3 scripts/analyze.py        # Statistical tests
python3 scripts/product_findings.py  # The three findings
python3 scripts/make_figures.py   # Figures (needs matplotlib)
bash    scripts/test_variance_gate.sh  # 9-scenario gate smoke test
```

## Directory layout

```
REPORT.md                # Main report
REPORT_summary.md        # Executive summary
findings.md              # §3: Static audit of codspeed-optimize SKILL.md
raw/                     # Unenriched bot-comment metadata (11 JSONL files)
parsed/                  # Enriched + structured bot comments (11 JSONL files)
labeled/                 # Diagnostic set + analysis outputs
figures/                 # 3 figures (forest plot, bootstrap, era × mode)
scripts/                 # 7 Python scripts + 1 shell test harness
```

## License

MIT. See [`LICENSE`](./LICENSE).
