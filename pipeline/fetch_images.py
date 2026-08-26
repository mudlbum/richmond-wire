#!/usr/bin/env python3
"""
Attach a Pexels photograph to each article in a day's edition.

Honest-attribution rules enforced here:
  * every photo carries the photographer's name and a link back to Pexels
  * every photo is labelled in the caption as an illustrative stock photo that
    does not depict the event
  * articles in sensitive categories, or matching the sensitive-topic list, get
    a generated section graphic instead of a photograph of real people, so we
    never place strangers' faces beside a disaster or a crime

Requires PEXELS_API_KEY (free: https://www.pexels.com/api/). Without it the
script exits cleanly and every article falls back to a section graphic.

    python3 pipeline/fetch_images.py content/2026-08-26
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

API = "https://api.pexels.com/v1/search"
UA = "RichmondNewswire/1.0 (+image fetcher)"

# Topics where a photograph of identifiable strangers next to the story would be
# misleading or undignified, no matter how careful the caption is.
SENSITIVE = re.compile(
    r"\b(kill|killed|dead|death|deaths|toll|massacre|attack|attacked|shooting|"
    r"bomb|bombing|strike|strikes|war|casualt|victim|victims|kidnap|hostage|"
    r"abduct|rape|assault|crash|earthquake|flood|famine|refugee|evacuat|"
    r"missing|drown|wounded|injured|funeral|grief|prisoner|detain)\b",
    re.IGNORECASE,
)

# Queries that reliably return objects, places and abstractions rather than
# faces — used as the search term for sensitive stories when we do search at all.
SAFE_FALLBACK = {
    "world": "united nations flags building",
    "environment": "mountain river valley landscape",
    "society": "empty road sunrise horizon",
    "economy": "shipping containers port",
    "technology": "circuit board macro",
    "science": "laboratory glassware",
    "sport": "empty stadium seats",
    "culture": "theatre stage lights",
}


def api_get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def key_works(key: str) -> tuple[bool, str]:
    """Check the credential once, loudly.

    Without this a wrong or expired key produces ten quiet per-query failures and
    an edition that silently falls back to gradients — which looks like "the
    images are broken" rather than "the key is wrong"."""
    q = urllib.parse.urlencode({"query": "sky", "per_page": 1})
    try:
        api_get(f"{API}?{q}", key)
        return True, ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, (f"Pexels rejected the API key (HTTP {e.code}). Check the "
                           f"PEXELS_API_KEY secret — it should be the key from "
                           f"pexels.com/api, with no quotes or stray spaces.")
        return False, f"Pexels returned HTTP {e.code} on a test request."
    except Exception as e:  # noqa: BLE001
        return False, f"Could not reach the Pexels API ({type(e).__name__}: {e})."


def search(query: str, key: str, *, orientation: str = "landscape") -> dict | None:
    q = urllib.parse.urlencode(
        {"query": query, "per_page": 12, "orientation": orientation, "size": "medium"})
    try:
        data = api_get(f"{API}?{q}", key)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  ! pexels error for {query!r}: {e}\n")
        return None
    photos = data.get("photos") or []
    if not photos:
        return None
    # Prefer wide images with a real photographer credit.
    photos.sort(key=lambda p: abs((p.get("width", 1) / max(p.get("height", 1), 1)) - 1.78))
    return photos[0]


def is_sensitive(article: dict) -> bool:
    hay = " ".join([article.get("headline", ""), article.get("dek", ""),
                    article.get("summary", "")])
    return bool(SENSITIVE.search(hay))


def choose_query(article: dict) -> str | None:
    """Return the query to search, or None to skip photographs entirely."""
    img = article.get("image") or {}
    queries = img.get("queries") or ([img["query"]] if img.get("query") else [])
    if is_sensitive(article):
        # Only ever use the neutral, object-focused fallback for these.
        return SAFE_FALLBACK.get(article.get("category", ""), None)
    return queries[0] if queries else SAFE_FALLBACK.get(article.get("category", ""))


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: fetch_images.py <content/YYYY-MM-DD>\n")
        return 2
    day_dir = Path(sys.argv[1])
    if not day_dir.is_dir():
        sys.stderr.write(f"not a directory: {day_dir}\n")
        return 2

    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        print("PEXELS_API_KEY not set — every article will use a section graphic.")
        print("This is a supported mode, not an error: the site builds fine "
              "without photographs.")
        return 0

    ok, why = key_works(key)
    if not ok:
        sys.stderr.write(f"{why}\n")
        sys.stderr.write("Refusing to continue: silently producing an edition of "
                         "gradients would look like a design choice rather than a "
                         "broken credential.\n")
        return 1
    print("Pexels API key accepted.")

    changed = 0
    fell_back = 0
    for jf in sorted(day_dir.glob("*.json")):
        if jf.name == "edition.json":
            continue
        article = json.loads(jf.read_text(encoding="utf-8"))
        img = article.setdefault("image", {})
        if img.get("url"):
            continue  # already has one

        query = choose_query(article)
        if not query:
            print(f"  · {jf.name}: no safe query — section graphic")
            fell_back += 1
            continue

        photo = search(query, key)
        time.sleep(0.4)  # be polite to the API
        if not photo:
            print(f"  · {jf.name}: no result for {query!r} — section graphic")
            fell_back += 1
            continue

        src = photo.get("src", {})
        img.update({
            "provider": "pexels",
            "pexels_id": photo.get("id"),
            "query_used": query,
            "url": src.get("large2x") or src.get("large") or src.get("original"),
            "thumb": src.get("large") or src.get("medium"),
            "photographer": photo.get("photographer"),
            "photographer_url": photo.get("photographer_url"),
            "page_url": photo.get("url"),
            "alt": img.get("alt") or (photo.get("alt")
                                      or f"Stock photograph: {query}"),
            "illustrative": True,
        })
        jf.write_text(json.dumps(article, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        changed += 1
        print(f"  ✓ {jf.name}: {query!r} → {photo.get('photographer')}")

    print(f"\nAttached {changed} photograph(s); {fell_back} article(s) kept a "
          f"section graphic.")
    if changed == 0 and fell_back:
        sys.stderr.write("No photographs were attached at all. The key works, so "
                         "this is a query problem — check the image.queries in the "
                         "article JSON.\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
