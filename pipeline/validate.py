#!/usr/bin/env python3
"""
Editorial gate. Run this before every build.

Checks each article against the rules in /editorial-standards/. Articles that fail
a HARD check are quarantined into content/<day>/_rejected/ rather than published,
so a bad story removes itself from the edition instead of taking the day down.

    python3 pipeline/validate.py content/2026-08-26
    python3 pipeline/validate.py content/2026-08-26 --check-links
    python3 pipeline/validate.py content/2026-08-26 --quarantine
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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

VALID_CATEGORIES = {"world", "economy", "technology", "science", "sport",
                    "culture", "environment", "society"}

# Phrases that signal editorialising, hype, or false certainty.
BANNED = [
    (r"\bsparks? (?:outrage|fury|backlash)\b", "tabloid framing"),
    (r"\bshocking\b|\bstunning(?:ly)?\b|\bbombshell\b", "hype adjective"),
    (r"(?<!Grand )\bslams\b|\bblasts\s+\w+|\brips? into\b", "tabloid verb"),
    (r"\bwe (?:believe|think|feel)\b|\bin my view\b|\bI think\b", "opinion in our voice"),
    (r"\bexperts? (?:say|agree|warn)\b(?!\s*[,:]?\s*[A-Z])", "unnamed authority"),
    (r"\bsources say\b|\breports suggest\b|\bit is understood\b", "unattributed claim"),
    (r"\bcure for\b|\bmiracle\b|\bbreakthrough that could cure\b", "medical overstatement"),
    (r"\bproves? that\b(?= .{0,60}\bstudy\b)", "causal overstatement"),
    (r"\bclick here\b|\bread more here\b", "SEO filler"),
    (r"\byou should (?:take|stop|start|try)\b", "health or financial advice"),
]

# Categories where a "what is not yet confirmed" section is mandatory.
DEVELOPING = re.compile(
    r"\b(kill|killed|dead|death|toll|missing|attack|strike|flood|earthquake|"
    r"crash|casualt|wounded|injured|evacuat|outbreak|ceasefire)\b", re.IGNORECASE)


def domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).replace("www.", "").lower() if m else ""


def text_of(article: dict) -> str:
    parts = [article.get("headline", ""), article.get("dek", ""),
             article.get("summary", "")]
    for b in article.get("body", []):
        parts.append(b.get("text", ""))
        parts.extend(str(i) for i in b.get("items", []))
    return "\n".join(parts)


def our_prose(article: dict) -> str:
    """Everything the newswire says in its own voice — quotations stripped out.

    Editorialising checks run against this, so a source who says 'I think' or
    'shocking' does not fail our own language rules."""
    parts = [article.get("headline", ""), article.get("dek", ""),
             article.get("summary", "")]
    for b in article.get("body", []):
        if b.get("type") == "quote":
            continue
        parts.append(b.get("text", ""))
        parts.extend(str(i) for i in b.get("items", []))
    joined = "\n".join(parts)
    return re.sub(r'"[^"]*"', " ", joined)


def check_link(url: str, timeout: int = 12) -> str | None:
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (compatible; RichmondNewswire link check)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return None if r.status < 400 else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        # Paywalls and bot walls commonly return these; the URL still exists.
        if e.code in (401, 403, 405, 429):
            return None
        return f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


def validate(article: dict, *, check_links: bool) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    body = article.get("body", [])
    blob = text_of(article)

    # --- structure -------------------------------------------------------
    for f in ("slug", "category", "headline", "dek", "summary", "body", "sources"):
        if not article.get(f):
            hard.append(f"missing required field: {f}")
    if article.get("category") not in VALID_CATEGORIES:
        hard.append(f"invalid category: {article.get('category')!r}")
    if len(body) < 5:
        hard.append(f"body has only {len(body)} block(s); minimum 5")
    words = len(blob.split())
    if words < 250:
        hard.append(f"body is {words} words; minimum 250")
    elif words > 1600:
        soft.append(f"body is {words} words; long for a daily digest")

    # --- sourcing --------------------------------------------------------
    urls = [s.get("url", "") for s in article.get("sources", [])]
    good = [u for u in urls if u.startswith("http")]
    domains = {domain(u) for u in good}
    if len(domains) < 2:
        hard.append(f"only {len(domains)} independent source domain(s); 2 required")
    for s in article.get("sources", []):
        if not s.get("outlet"):
            soft.append(f"source missing outlet name: {s.get('url', '?')}")
        if not s.get("title"):
            soft.append(f"source missing title: {s.get('url', '?')}")
    if check_links:
        for u in good:
            err = check_link(u)
            if err:
                hard.append(f"source URL unreachable ({err}): {u}")

    # --- quotes ----------------------------------------------------------
    for b in body:
        if b.get("type") == "quote" and not b.get("cite"):
            hard.append(f"quote without attribution: {b.get('text', '')[:60]}…")
    # Quoted strings inside paragraphs should generally carry an attribution nearby.
    for b in body:
        if b.get("type") == "p":
            t = b.get("text", "")
            if re.search(r'"[^"]{40,}"', t) and not re.search(
                    r"\b(said|says|told|telling|according to|wrote|via|quoted|speaking|carried|"
                    r"added|described|put it|reported|statement)\b", t, re.IGNORECASE):
                soft.append(f"long quotation without visible attribution: {t[:60]}…")

    # --- language --------------------------------------------------------
    prose = our_prose(article)
    for pattern, label in BANNED:
        m = re.search(pattern, prose, re.IGNORECASE)
        if m:
            soft.append(f"{label}: {m.group(0)!r}")

    # --- developing stories must declare uncertainty ----------------------
    if DEVELOPING.search(blob) and not article.get("uncertain"):
        hard.append("developing/casualty story with no 'uncertain' entries")

    # --- images ----------------------------------------------------------
    img = article.get("image") or {}
    if not (img.get("queries") or img.get("query")):
        soft.append("no image query; article will use a section graphic")
    if img.get("url") and not img.get("photographer"):
        hard.append("photograph present without photographer credit")

    # --- headline --------------------------------------------------------
    hl = article.get("headline", "")
    if len(hl) > 130:
        hard.append(f"headline is {len(hl)} characters; maximum 130")
    if hl.isupper():
        hard.append("headline is all caps")
    if hl.endswith("?") and not re.search(r"\b(will|whether|why|how|what)\b", hl, re.I):
        soft.append("headline is a rhetorical question")

    return hard, soft


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    ap.add_argument("--check-links", action="store_true",
                    help="fetch every source URL (slow, needs network)")
    ap.add_argument("--quarantine", action="store_true",
                    help="move failing articles to _rejected/ instead of exiting 1")
    args = ap.parse_args()

    day = Path(args.day_dir)
    if not day.is_dir():
        sys.stderr.write(f"not a directory: {day}\n")
        return 2

    files = [f for f in sorted(day.glob("*.json")) if f.name != "edition.json"]
    if not files:
        sys.stderr.write(f"no articles in {day}\n")
        return 2

    seen_slugs: dict[str, str] = {}
    failed, warned, passed = [], 0, 0

    for jf in files:
        try:
            article = json.loads(jf.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"✗ {jf.name}: invalid JSON — {e}")
            failed.append(jf)
            continue

        hard, soft = validate(article, check_links=args.check_links)
        slug = article.get("slug", "")
        if slug in seen_slugs:
            hard.append(f"duplicate slug, also used by {seen_slugs[slug]}")
        seen_slugs[slug] = jf.name

        if hard:
            print(f"✗ {jf.name}")
            for h in hard:
                print(f"    FAIL  {h}")
            failed.append(jf)
        else:
            passed += 1
            print(f"✓ {jf.name}")
        for s in soft:
            warned += 1
            print(f"    warn  {s}")

    print(f"\n{passed} passed, {len(failed)} failed, {warned} warning(s)")

    if failed and args.quarantine:
        q = day / "_rejected"
        q.mkdir(exist_ok=True)
        for jf in failed:
            jf.rename(q / jf.name)
        print(f"Quarantined {len(failed)} article(s) into {q}. "
              f"The edition will publish {passed} stories.")
        return 0 if passed else 1

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
