#!/usr/bin/env python3
"""
Print the research brief for one publishing cycle.

The wire runs every two hours. Each cycle is handed one beat and asked for two
stories, so a full day covers every beat several times over rather than dumping
ten stories at once. This script assembles what the desk needs to see:

  * the editorial rules (pipeline/editorial_prompt.md)
  * the beat for this hour, chosen by rotation
  * everything published in the last 14 days, so nothing is filed twice

    python pipeline/brief.py                  # the beat for the current hour
    python pipeline/brief.py --beat economy   # force a beat
    python pipeline/brief.py --count 2        # how many stories to ask for

Output goes to stdout. Nothing is written and no network call is made.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from coverage import coverage_brief  # noqa: E402

PROMPT_FILE = HERE / "editorial_prompt.md"

BEATS = {
    "world": {
        "name": "International conflict, diplomacy and humanitarian affairs",
        "brief": ("Wars, negotiations, ceasefires, aid operations, UN and treaty "
                  "matters, and elections with international consequence. Prefer "
                  "stories with a diplomatic or humanitarian dimension alongside "
                  "the hard news, without sugarcoating."),
    },
    "economy": {
        "name": "Global economy, trade, markets and business",
        "brief": ("Inflation, jobs, energy and food prices, central bank decisions, "
                  "trade agreements and disputes, and corporate developments with "
                  "real-world consequences. International, not US-only."),
    },
    "techsci": {
        "name": "Technology and science",
        "brief": ("One technology story and one science, space or medical-research "
                  "story. For research, cite the paper or agency release itself and "
                  "state its limitations."),
    },
    "sportculture": {
        "name": "International sport and global culture",
        "brief": ("One sport story and one culture, arts, heritage or major-event "
                  "story. Think globally, not US-centric. Get every score, name and "
                  "spelling exactly right."),
    },
    "envsociety": {
        "name": "Environment and society",
        "brief": ("One environment, climate or natural-disaster story, and one "
                  "society, health, education or human-interest story with "
                  "international resonance. For disasters use official figures from "
                  "named authorities and never name private victims."),
    },
}

# Twelve cycles a day, one every two hours. Two stories a cycle would flog one
# topic to death if every cycle chased the same news, so each slot is handed a
# different beat and a full day covers all of them.
#
# The rotation is keyed on the hour modulo five rather than a fixed timetable,
# so it still cycles through every beat if the schedule drifts onto odd hours or
# runs at some other interval. Over the twelve even hours of a day that works out
# at world 3, technology & science 3, and two each of the rest — the wire leads
# on world news, which is what it is for.
ORDER = ["world", "economy", "techsci", "sportculture", "envsociety"]


def beat_for(hour: int) -> str:
    return ORDER[hour % len(ORDER)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beat", default="", choices=[""] + list(BEATS))
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    day = args.date or now.date().isoformat()
    key = args.beat or beat_for(now.hour)
    beat = BEATS[key]

    print(f"<!-- cycle {now.isoformat(timespec='minutes')} · beat: {key} -->\n")
    print(PROMPT_FILE.read_text(encoding="utf-8")
          .replace("{DATE}", day)
          .replace("{BEAT_NAME}", beat["name"])
          .replace("{BEAT_BRIEF}", beat["brief"])
          .replace("{COUNT}", str(args.count))
          .replace("{RECENT_COVERAGE}", coverage_brief(days=args.days)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
