#!/usr/bin/env python3
"""
Promote every edition still marked "pending" to "approved".

Called by the publish workflow. An edition can only be pending on main because a
person merged its review pull request — the timeout path stamps "auto" on the
branch before merging, so it never arrives here pending.

This script therefore refuses to touch anything that is not exactly "pending".
That refusal is the safeguard: it is what makes "an editor read this" a claim the
site can evidence rather than a default.

    python3 scripts/release_pending.py --by dave
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"


def status_of(edition_file: Path) -> str:
    try:
        meta = json.loads(edition_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    return (meta.get("review") or {}).get("status", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--by", default="", help="who merged (recorded in the edition)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not CONTENT.is_dir():
        print("no content/ directory")
        return 0

    released = []
    for day_dir in sorted(CONTENT.iterdir()):
        f = day_dir / "edition.json"
        if not (day_dir.is_dir() and f.exists()):
            continue
        st = status_of(f)
        if st != "pending":
            if st:
                print(f"· {day_dir.name}: already '{st}' — untouched")
            continue
        if args.dry_run:
            print(f"would release {day_dir.name} as approved")
            released.append(day_dir.name)
            continue
        cmd = [sys.executable, str(ROOT / "scripts" / "stamp_review.py"), str(day_dir),
               "--status", "approved", "--if-pending"]
        if args.by:
            cmd += ["--by", args.by]
        subprocess.run(cmd, check=True)
        released.append(day_dir.name)

    if released:
        print(f"Released {len(released)} edition(s) as editor-approved: "
              + ", ".join(released))
    else:
        print("No pending editions to release.")

    # Tell the workflow whether anything changed, without parsing stdout.
    Path("/tmp/released_count").write_text(str(len(released)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
