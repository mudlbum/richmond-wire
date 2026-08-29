#!/usr/bin/env python3
"""
One publishing cycle, end to end.

The desk writes its stories to a JSON file. This runs everything that has to
happen afterwards, in the order that keeps the site honest:

    pull  →  file  →  de-duplicate  →  editorial gate  →  commit  →  push

and GitHub Actions does the rest: it attaches Pexels photographs, rebuilds the
site and deploys it.

    python scripts/publish_cycle.py drafts.json
    python scripts/publish_cycle.py drafts.json --no-push     # stop before pushing
    python scripts/publish_cycle.py --republish               # rebuild, file nothing

Every step is fail-safe. A story that duplicates the archive is quarantined into
content/<day>/_duplicates/, one that fails the editorial gate into
content/<day>/_rejected/, and the cycle publishes what is left. If nothing is
left, nothing is committed and the exit code is 1 — a quiet cycle is a correct
outcome, not an error to work around.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
PY = sys.executable or "python3"


def run(cmd: list[str], *, check: bool = True, quiet: bool = False) -> int:
    if not quiet:
        print(f"\n$ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8",
                       errors="replace", capture_output=True)
    out = (r.stdout or "") + (r.stderr or "")
    if out.strip():
        print(out.rstrip())
    if check and r.returncode != 0:
        raise SystemExit(f"\nStopped: `{' '.join(str(c) for c in cmd)}` "
                         f"exited {r.returncode}.")
    return r.returncode


def git(*args: str, check: bool = True) -> int:
    return run(["git", *args], check=check)


def live_articles(day_dir: Path) -> list[str]:
    if not day_dir.is_dir():
        return []
    return sorted(f.name for f in day_dir.glob("*.json") if f.name != "edition.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("drafts", nargs="?", default="",
                    help="JSON file holding this cycle's article array")
    ap.add_argument("--date", default="")
    ap.add_argument("--no-push", action="store_true",
                    help="do everything locally, leave the commit unpushed")
    ap.add_argument("--republish", action="store_true",
                    help="skip filing; just rebuild and redeploy what is on disk")
    args = ap.parse_args()

    if not args.drafts and not args.republish:
        ap.error("give me a drafts file, or --republish")

    day = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    day_dir = CONTENT / day
    before = set(live_articles(day_dir))

    print(f"=== Richmond wire · cycle for {day} ===")
    print(f"{len(before)} story(ies) already filed today.")

    # 1. Catch up with the remote first. Actions commits photographs back to
    #    main after every cycle, so a stale clone would push a conflict.
    git("pull", "--rebase", "--autostash")

    # 2. File this cycle's stories.
    if args.drafts:
        run([PY, "pipeline/file_stories.py", args.drafts, "--date", day])

        # 3. Against the archive: anything already covered is quarantined.
        run([PY, "pipeline/dedupe.py", str(Path("content") / day)], check=False)

        # 4. The editorial gate. --quarantine means a bad story removes itself
        #    rather than taking the cycle down with it.
        run([PY, "pipeline/validate.py", str(Path("content") / day),
             "--quarantine"], check=False)

    after = set(live_articles(day_dir))
    added = sorted(after - before)
    if args.drafts and not added:
        print("\nEvery story in this batch was a duplicate or failed the "
              "editorial gate. Nothing new to publish this cycle — that is the "
              "intended outcome, not a failure.")
        git("add", "content/")
        if git("diff", "--staged", "--quiet", check=False) != 0:
            git("commit", "-m",
                f"Wire cycle {day}: all candidates filtered, nothing published")
            if not args.no_push:
                git("push")
        return 1

    # 5. Prove the site still builds before anything leaves the machine.
    run([PY, "build.py"])
    run([PY, "scripts/check_links.py"])

    # 6. Commit and push. Actions attaches photographs, rebuilds and deploys.
    git("add", "content/")
    if git("diff", "--staged", "--quiet", check=False) == 0:
        print("\nNothing changed on disk. Nothing to push.")
        return 0

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M UTC")
    if added:
        subject = (f"Wire {day} {stamp}: {len(added)} new "
                   f"{'story' if len(added) == 1 else 'stories'}")
    else:
        subject = f"Rebuild the wire for {day}"
    git("commit", "-m", subject)

    if args.no_push:
        print("\nCommitted locally. --no-push, so nothing has gone live.")
        return 0

    git("push")
    print(f"\nPushed. GitHub Actions will attach photographs, rebuild and deploy.")
    print(f"Filed this cycle: {', '.join(added) if added else 'nothing new'}")
    print(f"Today's edition now carries {len(after)} story(ies), labelled "
          f"unreviewed until you read them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
