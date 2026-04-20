"""
Phase D: enrich raw PR metadata with bot comment body + PR outcome details.

For each PR in raw/*.jsonl:
  1. Fetch issue comments -> find codspeed-hq[bot] comment(s) -> keep the most
     recent one (== final body per Agent 1's amend-in-place concern, even if
     our spot-check didn't see any amends in practice).
  2. Fetch PR details (merged state, merge commit SHA, head SHA, commits count).
  3. Parse the bot comment with parse.py.

Strategy: iterate newest-first, early-stop per repo when we have enough
regression samples. Target: 15 confirmed regressions per repo, ceiling 150 PR
fetches per repo.

Output: parsed/{owner}__{repo}.jsonl — one record per PR that has a parseable
codspeed-hq[bot] comment.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from parse import parse, to_dict  # noqa: E402

TOKEN_PATH = pathlib.Path.home() / ".github_pat"
RAW_DIR = pathlib.Path("/home/dev/codspeed-atlas/raw")
OUT_DIR = pathlib.Path("/home/dev/codspeed-atlas/parsed")

TOKEN = TOKEN_PATH.read_text().strip()
BOT = "codspeed-hq[bot]"
CORE_SLEEP = 0.05


def api(url: str) -> list | dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codspeed-atlas-enrich",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_bot_comment(repo: str, number: int) -> dict | None:
    """Return the latest (highest updated_at) codspeed-hq[bot] comment or None."""
    # Pagination: most PRs have <30 comments, but some have >100.
    all_bot = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/issues/{number}/comments?per_page=100&page={page}"
        try:
            items = api(url)
        except Exception as e:
            print(f"  [warn] {repo}#{number} comments fetch failed: {e}", file=sys.stderr)
            return None
        if not isinstance(items, list):
            return None
        for c in items:
            if (c.get("user") or {}).get("login") == BOT:
                all_bot.append(c)
        if len(items) < 100:
            break
        page += 1
        time.sleep(CORE_SLEEP)
        if page > 10:
            break
    if not all_bot:
        return None
    # Return the most recently updated one (safest final-state heuristic)
    all_bot.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return all_bot[0]


def fetch_pr_detail(repo: str, number: int) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/pulls/{number}"
    try:
        return api(url)
    except Exception as e:
        print(f"  [warn] {repo}#{number} pr detail failed: {e}", file=sys.stderr)
        return None


def enrich_repo(raw_path: pathlib.Path, regression_target: int, ceiling: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / raw_path.name

    if out_path.exists():
        print(f"[skip] {raw_path.name} already enriched")
        return

    repo = raw_path.stem.replace("__", "/")
    rows = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]

    # Iterate newest-first (raw already desc by created_at)
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    records = []
    n_regressions = 0
    n_scanned = 0

    with out_path.open("w") as fout:
        for row in rows:
            if n_regressions >= regression_target or n_scanned >= ceiling:
                break
            if not row.get("pull_request"):
                continue  # skip non-PR issues
            number = row.get("number")
            if number is None:
                continue

            n_scanned += 1
            comment = fetch_bot_comment(repo, number)
            if comment is None:
                time.sleep(CORE_SLEEP)
                continue

            parsed = parse(comment.get("body", ""))
            pd = to_dict(parsed)

            pr_detail = None
            if pd["verdict"] == "degrade":
                pr_detail = fetch_pr_detail(repo, number)

            record = {
                "repo": repo,
                "number": number,
                "html_url": row.get("html_url"),
                "title": row.get("title"),
                "state": row.get("state"),
                "closed_at": row.get("closed_at"),
                "comment_id": comment.get("id"),
                "comment_created_at": comment.get("created_at"),
                "comment_updated_at": comment.get("updated_at"),
                "comment_edited": comment.get("updated_at") != comment.get("created_at"),
                "parsed": pd,
                "pr_detail": (
                    {
                        "merged": (pr_detail or {}).get("merged"),
                        "merged_at": (pr_detail or {}).get("merged_at"),
                        "merge_commit_sha": (pr_detail or {}).get("merge_commit_sha"),
                        "commits": (pr_detail or {}).get("commits"),
                        "changed_files": (pr_detail or {}).get("changed_files"),
                        "additions": (pr_detail or {}).get("additions"),
                        "deletions": (pr_detail or {}).get("deletions"),
                        "author": ((pr_detail or {}).get("user") or {}).get("login"),
                    }
                    if pr_detail
                    else None
                ),
            }
            records.append(record)
            fout.write(json.dumps(record, ensure_ascii=False) + "\n")
            fout.flush()

            if pd["verdict"] == "degrade":
                n_regressions += 1
                overall = pd.get("overall_pct")
                merged = record["pr_detail"] and record["pr_detail"].get("merged")
                print(
                    f"  [{repo}] #{number}: degrade {overall}% (merged={merged}) "
                    f"[{n_regressions}/{regression_target}]"
                )
            time.sleep(CORE_SLEEP)

    print(
        f"[{repo}] enriched {len(records)} PRs, {n_regressions} regressions, "
        f"scanned {n_scanned} -> {out_path}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=15, help="regressions per repo target")
    ap.add_argument("--ceiling", type=int, default=150, help="max PRs to scan per repo")
    ap.add_argument("--only", type=str, default=None, help="comma-separated repo slugs to enrich")
    args = ap.parse_args()

    raw_files = sorted(RAW_DIR.glob("*.jsonl"))
    if not raw_files:
        print("No raw files found. Run crawl.py first.", file=sys.stderr)
        sys.exit(1)

    only_set = {s.strip() for s in args.only.split(",")} if args.only else None

    for rp in raw_files:
        repo = rp.stem.replace("__", "/")
        if only_set is not None and repo not in only_set:
            continue
        try:
            enrich_repo(rp, args.target, args.ceiling)
        except Exception as e:
            print(f"[{rp.name}] ERROR: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
