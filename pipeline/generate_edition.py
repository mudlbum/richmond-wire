#!/usr/bin/env python3
"""
Generate one day's edition by calling the Claude API with web search enabled.

Runs one request per beat. Each request researches the last 48 hours on that beat
and returns article objects conforming to the schema in editorial_prompt.md.

    ANTHROPIC_API_KEY=sk-... python3 pipeline/generate_edition.py
    python3 pipeline/generate_edition.py --date 2026-08-26 --beats world,economy

Fails safe: a beat that errors, returns malformed JSON, or produces articles that
do not pass validation is skipped. A short edition is always better than a bad one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
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
PROMPT_FILE = Path(__file__).resolve().parent / "editorial_prompt.md"

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = int(os.environ.get("EDITION_MAX_TOKENS", "16000"))

# Two stories per beat, five beats, ten stories. Ranks are assigned after
# generation so the strongest story leads regardless of which beat produced it.
BEATS = {
    "world": {
        "name": "International conflict, diplomacy and humanitarian affairs",
        "brief": ("Wars, negotiations, ceasefires, aid operations, UN and treaty "
                  "matters, and elections with international consequence. Prefer "
                  "stories with a diplomatic or humanitarian dimension alongside "
                  "the hard news, without sugarcoating."),
        "count": 2,
    },
    "economy": {
        "name": "Global economy, trade, markets and business",
        "brief": ("Inflation, jobs, energy and food prices, central bank decisions, "
                  "trade agreements and disputes, and corporate developments with "
                  "real-world consequences. International, not US-only."),
        "count": 2,
    },
    "techsci": {
        "name": "Technology and science",
        "brief": ("One technology story and one science, space or medical-research "
                  "story. For research, cite the paper or agency release itself and "
                  "state its limitations."),
        "count": 2,
    },
    "sportculture": {
        "name": "International sport and global culture",
        "brief": ("One sport story and one culture, arts, heritage or major-event "
                  "story. Think globally, not US-centric. Get every score, name and "
                  "spelling exactly right."),
        "count": 2,
    },
    "envsociety": {
        "name": "Environment and society",
        "brief": ("One environment, climate or natural-disaster story, and one "
                  "society, health, education or human-interest story with "
                  "international resonance. For disasters use official figures from "
                  "named authorities and never name private victims."),
        "count": 2,
    },
}

VALID_CATEGORIES = {"world", "economy", "technology", "science", "sport",
                    "culture", "environment", "society"}


def call_claude(prompt: str, api_key: str, *, retries: int = 3) -> str:
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 24,
        }],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=data, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                body = json.loads(r.read().decode("utf-8"))
            return "".join(b.get("text", "") for b in body.get("content", [])
                           if b.get("type") == "text")
        except urllib.error.HTTPError as e:  # noqa: PERF203
            detail = e.read().decode("utf-8", "replace")[:400]
            last = RuntimeError(f"HTTP {e.code}: {detail}")
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                wait = 20 * (attempt + 1)
                sys.stderr.write(f"    retrying in {wait}s ({e.code})\n")
                time.sleep(wait)
                continue
            raise last
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(15)
                continue
            raise
    raise last or RuntimeError("unreachable")


def extract_json_array(text: str) -> list:
    """Pull the JSON array out of a model response, fences or no fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError("no JSON array found in response")
        text = text[start:end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    return parsed


def basic_check(a: dict) -> list[str]:
    """Cheap structural gate. pipeline/validate.py does the thorough pass."""
    problems = []
    for field in ("slug", "category", "headline", "dek", "summary", "body", "sources"):
        if not a.get(field):
            problems.append(f"missing {field}")
    if a.get("category") not in VALID_CATEGORIES:
        problems.append(f"bad category {a.get('category')!r}")
    srcs = [s for s in a.get("sources", []) if str(s.get("url", "")).startswith("http")]
    if len(srcs) < 2:
        problems.append(f"only {len(srcs)} sourced URL(s); 2 required")
    if len(a.get("body", [])) < 5:
        problems.append("body too short")
    if len(a.get("headline", "")) > 130:
        problems.append("headline too long")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--beats", default=",".join(BEATS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.stderr.write("ANTHROPIC_API_KEY is not set.\n")
        return 2

    day = args.date
    out_dir = CONTENT / day
    out_dir.mkdir(parents=True, exist_ok=True)
    template = PROMPT_FILE.read_text(encoding="utf-8")

    collected: list[dict] = []
    for beat_key in [b.strip() for b in args.beats.split(",") if b.strip()]:
        beat = BEATS.get(beat_key)
        if not beat:
            sys.stderr.write(f"unknown beat {beat_key!r}, skipping\n")
            continue
        print(f"→ {beat_key}: {beat['name']}")
        prompt = (template
                  .replace("{DATE}", day)
                  .replace("{BEAT_NAME}", beat["name"])
                  .replace("{BEAT_BRIEF}", beat["brief"])
                  .replace("{COUNT}", str(beat["count"])))
        try:
            raw = call_claude(prompt, api_key)
            articles = extract_json_array(raw)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"  ! {beat_key} failed: {e}\n")
            continue

        for a in articles:
            problems = basic_check(a)
            if problems:
                sys.stderr.write(f"  ! dropped {a.get('slug', '?')}: "
                                 f"{'; '.join(problems)}\n")
                continue
            a["date"] = day
            a["_beat"] = beat_key
            collected.append(a)
            print(f"  ✓ {a['category']}: {a['headline'][:70]}")

    if not collected:
        sys.stderr.write("No usable articles produced. Nothing written.\n")
        return 1

    # Rank: lead with world/environment/economy, then the rest, preserving order.
    priority = {"world": 0, "environment": 0, "economy": 1, "society": 2,
                "science": 3, "technology": 3, "sport": 4, "culture": 5}
    collected.sort(key=lambda a: priority.get(a["category"], 9))
    for i, a in enumerate(collected, start=1):
        a["rank"] = i
        a.pop("_beat", None)

    if args.dry_run:
        print(json.dumps(collected, ensure_ascii=False, indent=2)[:4000])
        return 0

    for a in collected:
        slug = re.sub(r"[^a-z0-9-]", "", a["slug"].lower())[:70].strip("-") or "story"
        a["slug"] = slug
        path = out_dir / f"{a['rank']:02d}-{slug}.json"
        path.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    (out_dir / "edition.json").write_text(json.dumps({
        "date": day,
        "count": len(collected),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": MODEL,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\nWrote {len(collected)} article(s) to {out_dir}")
    if len(collected) < 10:
        print(f"Note: {10 - len(collected)} story slot(s) unfilled. "
              f"Publishing short is the intended behaviour when verification fails.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
