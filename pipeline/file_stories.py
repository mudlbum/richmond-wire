#!/usr/bin/env python3
"""
File the stories from one publishing cycle into today's edition.

The desk (a Claude session on the editor's machine, running every two hours)
researches and writes the articles, then saves them as a JSON array. This script
is what turns that array into published content: it checks the structure, stamps
each story with the moment it was filed, numbers it, and drops it into
content/<day>/ alongside whatever earlier cycles filed today.

    python pipeline/file_stories.py drafts.json
    python pipeline/file_stories.py drafts.json --date 2026-08-29 --dry-run

Fails safe. A story missing sources, a category or a body is dropped and the rest
are filed. If nothing survives, nothing is written and the exit code is 1, so the
calling cycle stops before it commits an empty edition.

Deliberate: filing new stories resets the day's review stamp to "unreviewed".
The edition banner speaks for every story on the page, so a day an editor read at
noon cannot keep claiming review over stories filed at two. Approve at the end of
the day, with scripts/stamp_review.py, once the day is complete.
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

VALID_CATEGORIES = {"world", "economy", "technology", "science", "sport",
                    "culture", "environment", "society"}


def domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).replace("www.", "").lower() if m else ""


def basic_check(a: dict) -> list[str]:
    """Cheap structural gate. pipeline/validate.py does the thorough pass."""
    problems = []
    for field in ("slug", "category", "headline", "dek", "summary", "body", "sources"):
        if not a.get(field):
            problems.append(f"missing {field}")
    if a.get("category") not in VALID_CATEGORIES:
        problems.append(f"bad category {a.get('category')!r}")
    urls = [s.get("url", "") for s in a.get("sources", []) if isinstance(s, dict)]
    domains = {domain(u) for u in urls if u.startswith("http")}
    if len(domains) < 2:
        problems.append(f"only {len(domains)} independent source domain(s); 2 required")
    if len(a.get("body", [])) < 5:
        problems.append(f"body has {len(a.get('body', []))} block(s); minimum 5")
    if len(a.get("headline", "")) > 130:
        problems.append("headline over 130 characters")
    return problems


def existing_count(day_dir: Path) -> int:
    if not day_dir.is_dir():
        return 0
    return len([f for f in day_dir.glob("*.json") if f.name != "edition.json"])


def taken_slugs(day_dir: Path) -> set[str]:
    out: set[str] = set()
    if not day_dir.is_dir():
        return out
    for f in day_dir.glob("*.json"):
        if f.name == "edition.json":
            continue
        try:
            out.add(json.loads(f.read_text(encoding="utf-8")).get("slug", ""))
        except json.JSONDecodeError:
            continue
    return out


def stamp_unreviewed(day_dir: Path, filed: int) -> None:
    """Record the day as unreviewed, because new stories nobody has read just
    joined it. Never silently keeps an earlier 'approved'."""
    f = day_dir / "edition.json"
    meta = {}
    if f.exists():
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    was = (meta.get("review") or {}).get("status")
    now = dt.datetime.now(dt.timezone.utc)
    meta["date"] = day_dir.name
    meta["count"] = existing_count(day_dir)
    meta["last_filed_at"] = now.isoformat(timespec="seconds")
    meta["review"] = {
        "status": "unreviewed",
        "recorded_at": now.isoformat(timespec="seconds"),
        "note": "Filed automatically by the two-hourly wire; no editor has read "
                "these stories.",
    }
    f.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    if was == "approved":
        print("  note: this day was marked approved. Filing "
              f"{filed} unread story(ies) resets it to unreviewed — re-approve "
              "once you have read them.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drafts", help="JSON file holding an array of article objects")
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.drafts)
    if not src.is_file():
        sys.stderr.write(f"no such file: {src}\n")
        return 2
    try:
        drafts = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(f"{src} is not valid JSON: {e}\n")
        return 2
    if isinstance(drafts, dict):
        drafts = [drafts]
    if not isinstance(drafts, list) or not drafts:
        sys.stderr.write(f"{src} must hold a non-empty JSON array of articles\n")
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    day = args.date or now.date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        sys.stderr.write(f"bad date: {day}\n")
        return 2
    day_dir = CONTENT / day

    already = taken_slugs(day_dir)
    start = existing_count(day_dir)
    keep: list[dict] = []

    for a in drafts:
        if not isinstance(a, dict):
            sys.stderr.write("  ! dropped a draft that is not an object\n")
            continue
        problems = basic_check(a)
        if problems:
            sys.stderr.write(f"  ! dropped {a.get('slug', '?')}: "
                             f"{'; '.join(problems)}\n")
            continue
        slug = re.sub(r"[^a-z0-9-]", "", str(a["slug"]).lower())[:70].strip("-")
        if not slug:
            sys.stderr.write("  ! dropped a draft with an unusable slug\n")
            continue
        if slug in already:
            sys.stderr.write(f"  ! dropped {slug}: already filed today\n")
            continue
        a["slug"] = slug
        a["date"] = day
        a["published_at"] = now.isoformat(timespec="seconds")
        already.add(slug)
        keep.append(a)

    if not keep:
        sys.stderr.write("No usable stories in this batch. Nothing written.\n")
        return 1

    if args.dry_run:
        for i, a in enumerate(keep, start=start + 1):
            print(f"  would write {i:02d}-{a['slug']}.json — "
                  f"[{a['category']}] {a['headline'][:70]}")
        return 0

    day_dir.mkdir(parents=True, exist_ok=True)
    for i, a in enumerate(keep, start=start + 1):
        a["rank"] = i
        path = day_dir / f"{i:02d}-{a['slug']}.json"
        path.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"  + {path.name} — [{a['category']}] {a['headline'][:70]}")

    stamp_unreviewed(day_dir, len(keep))
    print(f"\nFiled {len(keep)} story(ies) into {day_dir.relative_to(ROOT)} "
          f"({existing_count(day_dir)} today so far).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
