"""
Crawl all codspeed-hq[bot] issue/PR comments across target repos via GitHub Search API.

Strategy:
- For each target repo, paginate search results ordered by creation date.
- Search API caps at 1000 results per query. If total_count > 1000 for a repo,
  we slice by created date windows to stay under the cap.
- Output: raw/{owner}__{repo}.jsonl, one comment metadata object per line.

Rate limits (auth'd): search=30/min, core=5000/hr. We sleep 2.1s between search
calls to stay safely under 30/min.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN_PATH = pathlib.Path.home() / ".github_pat"
OUT_DIR = pathlib.Path("/home/dev/codspeed-atlas/raw")

TARGETS = [
    ("biomejs", "biome"),
    ("langchain-ai", "langchain"),
    ("pydantic", "pydantic"),
    ("vercel", "next.js"),
    ("pydantic", "pydantic-core"),
    ("astral-sh", "ruff"),
    ("withastro", "astro"),
    ("fastapi", "fastapi"),
    ("astral-sh", "uv"),
    ("tursodatabase", "turso"),
    ("vercel", "turborepo"),
]

BOT = "codspeed-hq[bot]"
SEARCH_SLEEP = 2.1  # seconds between search calls (30/min cap)
CORE_SLEEP = 0.05   # polite gap between core calls

TOKEN = TOKEN_PATH.read_text().strip()


def api(url: str) -> tuple[dict, dict]:
    """Return (json_body, response_headers)."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codspeed-atlas-crawl",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
        return body, dict(resp.headers)


def search_window(repo: str, date_from: str | None, date_to: str | None) -> list[dict]:
    """Search for all bot comments in repo within [date_from, date_to]. Paginate."""
    q_parts = [f"commenter:{BOT}", f"repo:{repo}"]
    if date_from and date_to:
        q_parts.append(f"created:{date_from}..{date_to}")
    elif date_from:
        q_parts.append(f"created:>={date_from}")
    elif date_to:
        q_parts.append(f"created:<={date_to}")
    q = " ".join(q_parts)
    out = []
    page = 1
    while True:
        url = (
            "https://api.github.com/search/issues?"
            + urllib.parse.urlencode({"q": q, "per_page": 100, "page": page, "sort": "created", "order": "desc"})
        )
        body, _hdrs = api(url)
        items = body.get("items", [])
        total = body.get("total_count", 0)
        out.extend(items)
        if len(items) < 100 or len(out) >= total:
            break
        page += 1
        if page > 10:  # API caps at 1000 results
            break
        time.sleep(SEARCH_SLEEP)
    return out


def crawl_repo(owner: str, repo: str) -> None:
    slug = f"{owner}/{repo}"
    out_path = OUT_DIR / f"{owner}__{repo}.jsonl"
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"[skip] {slug} (already crawled at {out_path})")
        return

    # First, cheap count
    count_url = (
        "https://api.github.com/search/issues?"
        + urllib.parse.urlencode({"q": f"commenter:{BOT} repo:{slug}", "per_page": 1})
    )
    body, _ = api(count_url)
    total = body.get("total_count", 0)
    print(f"[{slug}] total_count = {total}")
    time.sleep(SEARCH_SLEEP)

    all_items: list[dict] = []
    seen_ids: set[int] = set()

    if total <= 1000:
        items = search_window(slug, None, None)
        for it in items:
            if it["id"] not in seen_ids:
                all_items.append(it)
                seen_ids.add(it["id"])
        time.sleep(SEARCH_SLEEP)
    else:
        # Slice by year-month windows descending until we cover all.
        # Bot only exists since ~2022. Walk back month-by-month.
        now = datetime.now(timezone.utc).replace(day=1)
        cursor = now
        collected = 0
        while collected < total and cursor.year >= 2021:
            month_start = cursor
            month_end = (cursor + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            date_from = month_start.strftime("%Y-%m-%d")
            date_to = month_end.strftime("%Y-%m-%d")
            items = search_window(slug, date_from, date_to)
            new = [it for it in items if it["id"] not in seen_ids]
            for it in new:
                seen_ids.add(it["id"])
                all_items.append(it)
            collected = len(all_items)
            print(f"  [{slug}] window {date_from}..{date_to}: +{len(new)} (cum {collected}/{total})")
            cursor = (cursor - timedelta(days=1)).replace(day=1)
            time.sleep(SEARCH_SLEEP)
            if len(items) == 0 and collected >= total * 0.99:
                break

    # Write raw issue/PR hits. We still need comment bodies (search hits point to
    # the issue/PR, not the specific bot comment). Leave body fetch for parse step.
    with out_path.open("w") as f:
        for it in all_items:
            record = {
                "repo": slug,
                "number": it.get("number"),
                "html_url": it.get("html_url"),
                "state": it.get("state"),
                "title": it.get("title"),
                "created_at": it.get("created_at"),
                "updated_at": it.get("updated_at"),
                "closed_at": it.get("closed_at"),
                "pull_request": it.get("pull_request"),
                "user_login": (it.get("user") or {}).get("login"),
                "labels": [l.get("name") for l in (it.get("labels") or [])],
            }
            f.write(json.dumps(record) + "\n")

    print(f"[{slug}] wrote {len(all_items)} records to {out_path}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for owner, repo in TARGETS:
        try:
            crawl_repo(owner, repo)
        except Exception as e:
            print(f"[{owner}/{repo}] ERROR: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
