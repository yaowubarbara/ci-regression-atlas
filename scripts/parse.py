"""
Parse a codspeed-hq[bot] comment body into structured fields.

Designed against the 2026 schema (ruff#24703) but tolerant of older variants
(pydantic#10845 older "degrade performances" phrasing, install variant).

Extracted fields:
  - verdict: one of {"degrade", "improve", "maintain", "install", "unknown"}
  - overall_pct: float or None
  - n_regressed, n_improved, n_untouched, n_skipped: int or None
  - base_sha, head_sha, base_ref, head_ref: str or None
  - changes: list[{mode, benchmark, base_time, head_time, efficiency_pct, benchmark_path}]
  - dashboard_url: str or None
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from urllib.parse import unquote


MARKER = "<!-- __CODSPEED_PERFORMANCE_REPORT_COMMENT__ -->"


@dataclass
class ParsedComment:
    verdict: str = "unknown"
    overall_pct: float | None = None
    n_regressed: int | None = None
    n_improved: int | None = None
    n_untouched: int | None = None
    n_skipped: int | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    changes: list[dict] = field(default_factory=list)
    dashboard_url: str | None = None
    schema_flags: list[str] = field(default_factory=list)


def is_codspeed_comment(body: str) -> bool:
    if not body:
        return False
    if MARKER in body:
        return True
    # Fallback for very old comments that may lack the marker
    return (
        ("CodSpeed Performance Report" in body)
        or ("degrade performance" in body)
        or ("degrade performances" in body)
        or ("CodSpeed is installed" in body)
    )


def parse(body: str) -> ParsedComment:
    p = ParsedComment()
    if not is_codspeed_comment(body):
        return p

    if MARKER in body:
        p.schema_flags.append("has_marker")

    # --- verdict + overall_pct ------------------------------------------
    # 2026: "Merging this PR will **degrade performance by 5.07%**"
    # 2024: "Merging #NNN into main will degrade performances by 45.41%"
    # Improve: "improve performance by X%"
    # Install variant: "Congrats! CodSpeed is installed"
    # Maintain: "This PR maintains performance" or "performance is unchanged"
    m = re.search(
        r"(degrade\s+performance(?:s)?|improve\s+performance(?:s)?|maintain\s+performance)"
        r"(?:\s+by\s+\*{0,2}\s*([-+]?\d+(?:\.\d+)?)\s*%\*{0,2})?",
        body,
        re.IGNORECASE,
    )
    if m:
        verb = m.group(1).lower()
        if verb.startswith("degrade"):
            p.verdict = "degrade"
        elif verb.startswith("improve"):
            p.verdict = "improve"
        else:
            p.verdict = "maintain"
        if m.group(2):
            try:
                p.overall_pct = float(m.group(2))
            except ValueError:
                pass
    elif re.search(r"CodSpeed is installed", body, re.IGNORECASE):
        p.verdict = "install"
    elif re.search(r"new benchmarks? (?:were|was) detected", body, re.IGNORECASE):
        p.verdict = "install"

    # --- counts ----------------------------------------------------------
    # `❌ 1` regressed benchmark
    # `✅ 46` untouched benchmarks
    # `⏩ 60` skipped benchmarks
    # Also handle non-emoji fallback "1 regressed / 46 untouched"
    for label, attr, pattern in [
        ("regressed", "n_regressed", r"`?❌\s*(\d+)`?\s*regressed"),
        ("improved", "n_improved", r"`?🚀\s*(\d+)`?\s*improved"),
        ("untouched", "n_untouched", r"`?✅\s*(\d+)`?\s*untouched"),
        ("skipped", "n_skipped", r"`?⏩\s*(\d+)`?\s*skipped"),
    ]:
        m = re.search(pattern, body)
        if m:
            setattr(p, attr, int(m.group(1)))

    # --- base/head SHA and ref ------------------------------------------
    # Comparing <code>charlie/unpack-literal</code> (7b49ce5) with <code>main</code> (e771b14)
    m = re.search(
        r"Comparing\s*<code>([^<]+)</code>\s*\(([0-9a-f]{6,40})\)\s*with\s*<code>([^<]+)</code>\s*\(([0-9a-f]{6,40})\)",
        body,
    )
    if m:
        p.head_ref = m.group(1)
        p.head_sha = m.group(2)
        p.base_ref = m.group(3)
        p.base_sha = m.group(4)

    # --- performance table rows -----------------------------------------
    # Two schemas observed:
    #   6-col (WallTime mode, e.g. ruff):
    #     | ❌ | WallTime | [`` pydantic ``](URL) | 7.7 s | 8.2 s | -5.07% |
    #   5-col (Simulation mode, e.g. pydantic):
    #     | ❌ | [`` bench ``](URL) | 635.2 µs | 699.8 µs | -9.23% |
    table_rx_6col = re.compile(
        r"^\|\s*(?P<emoji>[^|]*?)\s*"
        r"\|\s*(?P<mode>WallTime|Instrumentation|Simulation|[A-Za-z]+)\s*"
        r"\|\s*(?P<bench_cell>\[[^\]]+\][^|]+?)\s*"
        r"\|\s*(?P<base>[^|]+?)\s*"
        r"\|\s*(?P<head>[^|]+?)\s*"
        r"\|\s*(?P<eff>[-+]?\d+(?:\.\d+)?%)\s*\|",
        re.MULTILINE,
    )
    table_rx_5col = re.compile(
        r"^\|\s*(?P<emoji>[^|]*?)\s*"
        r"\|\s*(?P<bench_cell>\[[^\]]+\][^|]+?)\s*"
        r"\|\s*(?P<base>[^|]+?)\s*"
        r"\|\s*(?P<head>[^|]+?)\s*"
        r"\|\s*(?P<eff>[-+]?\d+(?:\.\d+)?%)\s*\|",
        re.MULTILINE,
    )
    six_matches = list(table_rx_6col.finditer(body))
    if six_matches:
        matches = six_matches
        schema_mode = "6col"
    else:
        matches = list(table_rx_5col.finditer(body))
        schema_mode = "5col" if matches else None
    if schema_mode:
        p.schema_flags.append(f"table_{schema_mode}")

    for m in matches:
        cell = m.group("bench_cell").strip()
        # Extract benchmark name and URL if present
        link_m = re.match(r"\[`{0,2}\s*(.+?)\s*`{0,2}\]\((.+?)\)", cell)
        if link_m:
            name = link_m.group(1).strip()
            url = link_m.group(2).strip()
        else:
            name = cell.strip("` ")
            url = None

        # Extract benchmark path from URL's ?uri= parameter
        bench_path = None
        if url:
            um = re.search(r"[?&]uri=([^&]+)", url)
            if um:
                bench_path = unquote(um.group(1))

        eff_str = m.group("eff").replace("%", "").strip()
        try:
            eff_val = float(eff_str)
        except ValueError:
            eff_val = None

        if schema_mode == "6col":
            mode_val = m.group("mode")
        else:
            mode_val = "Simulation"  # 5-col table implies simulation-only
        p.changes.append(
            {
                "mode": mode_val,
                "benchmark": name,
                "benchmark_path": bench_path,
                "base_time": m.group("base").strip(),
                "head_time": m.group("head").strip(),
                "efficiency_pct": eff_val,
                "regressed": "❌" in m.group("emoji") or (eff_val is not None and eff_val < 0),
            }
        )

    # --- dashboard url ---------------------------------------------------
    m = re.search(r"\(https://codspeed\.io/([^)\s]+?)/branches/([^?)\s]+)[^)]*\)", body)
    if m:
        p.dashboard_url = f"https://codspeed.io/{m.group(1)}/branches/{m.group(2)}"

    return p


def to_dict(p: ParsedComment) -> dict:
    return asdict(p)


if __name__ == "__main__":
    import sys, json
    body = sys.stdin.read()
    p = parse(body)
    print(json.dumps(to_dict(p), indent=2, ensure_ascii=False))
