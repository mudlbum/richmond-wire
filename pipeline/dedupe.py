#!/usr/bin/env python3
"""
Reject stories the newswire has already published.

Runs after generation, before the editorial gate. Two passes:

  1. Against the archive — anything that restates an earlier article is dropped,
     UNLESS it declares itself a follow-up and says what is new. A declared
     follow-up that is still ~three-quarters the same text is a rewrite, not a
     development, and is dropped anyway.

  2. Against itself — two beats can independently reach the same story on a busy
     day. The higher-ranked one survives.

Dropped articles are moved to content/<day>/_duplicates/ so the reviewer can see
what was filtered and why, rather than silently receiving a short edition.

    python3 pipeline/dedupe.py content/2026-08-27
    python3 pipeline/dedupe.py content/2026-08-27 --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coverage import (  # noqa: E402
    REBUILD, article_text, domain_paths, jaccard, load_published, tokens,
    worst_match,
)

# Two stories in the SAME edition need a lower bar than across days: there is no
# "developing story" defence for filing the same thing twice on one morning.
SAME_EDITION_TEXT = 0.42
SAME_EDITION_SOURCE = 0.30


def load_day(day_dir: Path) -> list[dict]:
    out = []
    for jf in sorted(day_dir.glob("*.json")):
        if jf.name == "edition.json":
            continue
        try:
            a = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  ! {jf.name}: unreadable JSON ({e})\n")
            continue
        a["_path"] = jf
        a["_file"] = jf.name
        a["_tokens"] = tokens(article_text(a))
        a["_sources"] = domain_paths(a)
        out.append(a)
    out.sort(key=lambda x: x.get("rank", 99))
    return out


def follow_up_ok(a: dict, hit: dict) -> tuple[bool, str]:
    """A follow-up is allowed only if the article says so explicitly, points at
    what it follows, and states what changed."""
    fu = a.get("follow_up") or {}
    whats_new = str(fu.get("whats_new", "")).strip()
    if not whats_new:
        return False, "not declared as a follow-up"
    if len(whats_new.split()) < 6:
        return False, "follow-up does not say what is new in any detail"
    if hit["is_rebuild"]:
        return False, (f"declared a follow-up, but {hit['text']:.0%} of the text is "
                       f"the same — that is a rewrite, not a development")
    return True, ""


def check_against_archive(day: str, articles: list[dict]) -> list[tuple[dict, dict, str]]:
    published = load_published(exclude_day=day)
    drops = []
    for a in articles:
        hit = worst_match(a, published, day)
        if not hit:
            continue
        ok, why = follow_up_ok(a, hit)
        if ok:
            # Record the link so the page can show it and the reviewer can check.
            a.setdefault("follow_up", {})["of_day"] = hit["prior_day"]
            a["follow_up"].setdefault("of", hit["prior_slug"])
            a["follow_up"]["_matched_headline"] = hit["prior_headline"]
            print(f"  ~ {a['_file']}: follow-up to {hit['prior_day']} "
                  f"'{hit['prior_headline'][:56]}' ({hit['text']:.0%} text) — kept")
            continue
        drops.append((a, hit, why))
    return drops


def check_within_edition(articles: list[dict]) -> list[tuple[dict, dict, str]]:
    drops, kept = [], []
    for a in articles:
        clash = None
        for k in kept:
            t = jaccard(a["_tokens"], k["_tokens"])
            s = jaccard(a["_sources"], k["_sources"])
            if t >= SAME_EDITION_TEXT or (s >= SAME_EDITION_SOURCE and t >= 0.25):
                clash = {"prior_day": "this edition", "prior_slug": k.get("slug", ""),
                         "prior_headline": k.get("headline", ""), "text": t,
                         "sources": s, "gap_days": 0,
                         "reason": f"same story as {k['_file']} "
                                   f"({t:.0%} text, {s:.0%} sources)",
                         "is_rebuild": True}
                break
        if clash:
            drops.append((a, clash, "duplicate within the same edition"))
        else:
            kept.append(a)
    return drops


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-keep", type=int, default=0,
                    help="fail with exit 2 if fewer than this many survive")
    args = ap.parse_args()

    day_dir = Path(args.day_dir)
    if not day_dir.is_dir():
        sys.stderr.write(f"not a directory: {day_dir}\n")
        return 2
    day = day_dir.name

    articles = load_day(day_dir)
    if not articles:
        sys.stderr.write(f"no articles in {day_dir}\n")
        return 2
    print(f"Checking {len(articles)} article(s) in {day} against previous editions.")

    drops = check_against_archive(day, articles)
    dropped_files = {a["_file"] for a, _, _ in drops}
    survivors = [a for a in articles if a["_file"] not in dropped_files]
    drops += check_within_edition(survivors)

    if not drops:
        print(f"No duplicates. All {len(articles)} article(s) are new.")
        # Persist any follow-up links we resolved.
        if not args.dry_run:
            for a in articles:
                if a.get("follow_up", {}).get("of_day"):
                    save(a)
        return 0

    quarantine = day_dir / "_duplicates"
    for a, hit, why in drops:
        print(f"  ✗ {a['_file']}")
        print(f"      {a.get('headline','')[:88]}")
        print(f"      duplicates {hit['prior_day']}: {hit['prior_headline'][:72]}")
        print(f"      {hit['reason']} — {why}")
        if not args.dry_run:
            quarantine.mkdir(exist_ok=True)
            a["_duplicate_of"] = {k: hit[k] for k in
                                  ("prior_day", "prior_slug", "prior_headline",
                                   "text", "sources", "reason")}
            a["_dropped_because"] = why
            payload = {k: v for k, v in a.items() if not k.startswith("_path")}
            payload.pop("_tokens", None)
            payload.pop("_sources", None)
            payload.pop("_file", None)
            (quarantine / a["_file"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            a["_path"].unlink()

    remaining = len(articles) - len(drops)
    print(f"\nDropped {len(drops)} duplicate(s). {remaining} article(s) remain.")
    if not args.dry_run:
        for a in articles:
            if a["_file"] not in {d[0]["_file"] for d in drops} \
                    and a.get("follow_up", {}).get("of_day"):
                save(a)
        print(f"Dropped articles kept in {quarantine} so the reviewer can see them.")

    if args.min_keep and remaining < args.min_keep:
        sys.stderr.write(f"only {remaining} article(s) left, below --min-keep "
                         f"{args.min_keep}\n")
        return 2
    return 0


def save(a: dict) -> None:
    payload = {k: v for k, v in a.items() if not k.startswith("_")}
    if "follow_up" in payload:
        payload["follow_up"] = {k: v for k, v in payload["follow_up"].items()
                                if not k.startswith("_")}
    a["_path"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
