#!/usr/bin/env python3
"""
What the newswire has already published, and how similar two stories are.

Two jobs:
  1. Build a compact index of past coverage to hand the researcher, so it knows
     what NOT to file again.
  2. Score similarity between a candidate article and past ones, so a duplicate
     that slips through anyway is caught before publication.

The hard problem is that "duplicate" and "follow-up" look almost identical to a
string comparison. A flood story on day two IS mostly the same words as day one.
The difference is whether it carries new facts. We cannot measure that
mechanically, so the design is: block anything that looks like a rebuild of an
earlier story, and let it through only when the article explicitly declares
itself a follow-up and says what changed. That declaration is a claim we render
on the page, so it costs something to make falsely.

    python3 pipeline/coverage.py --days 14        # what the prompt will see
    python3 pipeline/coverage.py --stats
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"

# Words too common in news writing to signal that two stories are the same one.
STOP = {
    "the","a","an","and","or","but","of","to","in","on","at","for","from","by",
    "with","as","is","are","was","were","be","been","being","has","have","had",
    "it","its","this","that","these","those","they","them","their","he","she",
    "his","her","we","our","you","your","i","not","no","than","then","so","if",
    "after","before","during","while","when","where","which","who","whom","what",
    "how","why","said","says","say","told","according","reported","report",
    "reports","new","news","first","last","more","most","other","some","any",
    "one","two","three","over","under","about","into","out","up","down","off",
    "per","cent","percent","year","years","day","days","week","weeks","month",
    "months","people","also","would","could","should","may","might","will",
    "can","there","here","now","still","yet","between","among","against",
}

WORD = re.compile(r"[a-z][a-z'-]{2,}")


def tokens(text: str) -> set[str]:
    """Content words only. Numbers are dropped deliberately: a casualty figure
    changing from 8 to 22 is exactly the case where the stories ARE the same."""
    return {w for w in WORD.findall((text or "").lower()) if w not in STOP}


def article_text(a: dict) -> str:
    parts = [a.get("headline", ""), a.get("dek", ""), a.get("summary", "")]
    parts.extend(str(t) for t in a.get("tags", []))
    for b in a.get("body", [])[:6]:          # the opening carries the subject
        parts.append(b.get("text", ""))
        parts.extend(str(i) for i in b.get("items", []))
    return "\n".join(parts)


def domain_paths(a: dict) -> set[str]:
    """Source identity: host + path, so two different stories from the same
    outlet do not look related just because both cite reuters.com."""
    out = set()
    for s in a.get("sources", []):
        m = re.match(r"https?://([^/]+)(/[^?#]*)?", s.get("url", ""))
        if not m:
            continue
        host = m.group(1).replace("www.", "").lower()
        path = (m.group(2) or "/").rstrip("/").lower()
        out.add(host + path)
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_published(exclude_day: str | None = None) -> list[dict]:
    """Every article on disk, newest first. `exclude_day` skips the edition
    currently being generated so it is not compared against itself."""
    out = []
    if not CONTENT.is_dir():
        return out
    for day_dir in sorted(CONTENT.iterdir(), reverse=True):
        if not day_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name):
            continue
        if exclude_day and day_dir.name == exclude_day:
            continue
        for jf in sorted(day_dir.glob("*.json")):
            if jf.name == "edition.json":
                continue
            try:
                a = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            a["_day"] = day_dir.name
            a["_file"] = jf.name
            a["_tokens"] = tokens(article_text(a))
            a["_sources"] = domain_paths(a)
            out.append(a)
    return out


def days_between(a: str, b: str) -> int:
    try:
        return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)
    except ValueError:
        return 999


# --------------------------------------------------------------------------
# similarity
# --------------------------------------------------------------------------
DUP_TEXT = 0.50          # same subject, restated
DUP_SOURCE = 0.34        # citing the same specific articles
DUP_TEXT_WITH_SOURCE = 0.30
REBUILD = 0.72           # so similar that even a declared follow-up is a rewrite
WINDOW_DAYS = 21         # how far back a duplicate can reach


def compare(candidate: dict, prior: dict, day: str) -> dict | None:
    """Return a verdict dict when `candidate` looks like `prior`, else None."""
    gap = days_between(day, prior["_day"])
    if gap > WINDOW_DAYS:
        return None

    cand_tokens = candidate.get("_tokens") or tokens(article_text(candidate))
    cand_sources = candidate.get("_sources") or domain_paths(candidate)

    t = jaccard(cand_tokens, prior["_tokens"])
    s = jaccard(cand_sources, prior["_sources"])

    same_slug = candidate.get("slug") and candidate["slug"] == prior.get("slug")

    reason = None
    if same_slug:
        reason = "identical slug"
    elif t >= DUP_TEXT:
        reason = f"text overlap {t:.0%}"
    elif s >= DUP_SOURCE and t >= DUP_TEXT_WITH_SOURCE:
        reason = f"cites the same reporting ({s:.0%} of sources, {t:.0%} text)"

    if not reason:
        return None
    return {
        "prior_day": prior["_day"],
        "prior_slug": prior.get("slug", ""),
        "prior_headline": prior.get("headline", ""),
        "text": t,
        "sources": s,
        "gap_days": gap,
        "reason": reason,
        "is_rebuild": t >= REBUILD or bool(same_slug),
    }


def worst_match(candidate: dict, published: list[dict], day: str) -> dict | None:
    hits = [h for h in (compare(candidate, p, day) for p in published) if h]
    if not hits:
        return None
    return max(hits, key=lambda h: (h["is_rebuild"], h["text"], h["sources"]))


# --------------------------------------------------------------------------
# the briefing handed to the researcher
# --------------------------------------------------------------------------
def coverage_brief(days: int = 14, limit: int = 90) -> str:
    today = dt.date.today().isoformat()
    published = load_published()
    recent = [a for a in published if days_between(today, a["_day"]) <= days]
    if not recent:
        return ("Nothing has been published yet, so there is no prior coverage to "
                "avoid. Every story is fair game.")

    by_day: dict[str, list[dict]] = {}
    for a in recent[:limit]:
        by_day.setdefault(a["_day"], []).append(a)

    lines = [
        f"We have already published the following in the last {days} days. "
        f"**Do not file any of these again.**",
        "",
    ]
    for day in sorted(by_day, reverse=True):
        lines.append(f"{day}:")
        for a in sorted(by_day[day], key=lambda x: x.get("rank", 99)):
            tags = ", ".join(str(t) for t in a.get("tags", [])[:4])
            lines.append(f"  - [{a.get('category','?')}] {a.get('headline','')}"
                         + (f"  (tags: {tags})" if tags else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        pub = load_published()
        days = sorted({a["_day"] for a in pub})
        print(f"{len(pub)} article(s) across {len(days)} edition(s)")
        if days:
            print(f"earliest {days[0]}  latest {days[-1]}")
        cats: dict[str, int] = {}
        for a in pub:
            cats[a.get("category", "?")] = cats.get(a.get("category", "?"), 0) + 1
        for c, n in sorted(cats.items(), key=lambda kv: -kv[1]):
            print(f"  {c:12s} {n}")
        return 0

    print(coverage_brief(args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
