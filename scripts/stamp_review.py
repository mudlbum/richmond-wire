#!/usr/bin/env python3
"""
Record what actually happened to an edition's review, in content/<day>/edition.json.

This file is the only evidence the site has that a person read an edition, so it is
the only thing allowed to make the site say so. Nothing else may set "approved".

    python3 scripts/stamp_review.py content/2026-08-26 --status pending
    python3 scripts/stamp_review.py content/2026-08-26 --status approved --by dave
    python3 scripts/stamp_review.py content/2026-08-26 --status auto
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

VALID = ("pending", "approved", "auto", "unreviewed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    ap.add_argument("--status", required=True, choices=VALID)
    ap.add_argument("--by", default="")
    ap.add_argument("--if-pending", action="store_true",
                    help="only change the status if it is currently pending")
    args = ap.parse_args()

    day_dir = Path(args.day_dir)
    if not day_dir.is_dir():
        sys.stderr.write(f"not a directory: {day_dir}\n")
        return 2

    f = day_dir / "edition.json"
    meta = {}
    if f.exists():
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}

    current = (meta.get("review") or {}).get("status")
    if args.if_pending and current not in (None, "pending"):
        print(f"edition is already '{current}' — leaving it alone")
        return 0

    now = dt.datetime.now(dt.timezone.utc)
    review = {"status": args.status,
              "recorded_at": now.isoformat(timespec="seconds")}
    if args.status == "approved":
        review["reviewed_at"] = now.strftime("%d %B %Y")
        if args.by:
            review["reviewed_by"] = args.by
    elif args.status == "auto":
        review["note"] = "review window expired; published without editorial review"

    meta.setdefault("date", day_dir.name)
    meta["review"] = review
    f.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{f}: review.status = {args.status}"
          + (f" (by {args.by})" if args.by else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
