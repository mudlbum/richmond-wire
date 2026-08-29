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

# Twelve cycles a day, one every two hours (UTC). The rotation is deliberately
# not a plain repeat of the five beats: it front-loads world and economy during
# the hours when most of the world's newsrooms are filing, and gives sport and
# culture the slots where their results actually land.
ROTATION = {
    0:  "world",        2:  "sportculture", 4:  "techsci",
    6:  "world",        8:  "economy",      10: "envsociety",
    12: "world",        14: "economy",      16: "techsci",
    18: "envsociety",   20: "sportculture", 22: "economy",
}


def beat_for(hour: int) -> str:
    # Any odd or off-schedule hour falls back to the slot below it.
    for h in range(hour, -1, -1):
        if h in ROTATION:
            return ROTATION[h]
    return "world"


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
