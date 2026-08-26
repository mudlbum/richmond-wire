#!/usr/bin/env python3
"""
Post-build check: every internal link in dist/ must resolve to a real file, and
every page must carry the things Google AdSense expects to find.

    python3 scripts/check_links.py
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def base_path() -> str:
    try:
        url = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))["url"]
    except Exception:  # noqa: BLE001
        return ""
    m = re.match(r"^https?://[^/]+(/.*)?$", url.rstrip("/"))
    return (m.group(1) or "").rstrip("/") if m else ""


BASE = base_path()
REQUIRED_FOOTER_LINKS = [f"{BASE}/about/", f"{BASE}/contact/",
                         f"{BASE}/privacy/", f"{BASE}/terms/"]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.imgs_without_alt = 0
        self.has_h1 = False
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a" and d.get("href"):
            self.hrefs.append(d["href"])
        if tag in ("img", "script", "link") and (d.get("src") or d.get("href")):
            self.srcs.append(d.get("src") or d.get("href", ""))
        if tag == "img" and not d.get("alt"):
            self.imgs_without_alt += 1
        if tag == "h1":
            self.has_h1 = True
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def resolve(target: str) -> Path | None:
    t = target.split("#")[0].split("?")[0]
    if not t or t.startswith(("http://", "https://", "mailto:", "tel:", "//")):
        return None
    # Strip the base path back off before resolving against dist/.
    if BASE and t.startswith(BASE + "/"):
        t = t[len(BASE):]
    elif BASE and t == BASE:
        t = "/"
    elif t.startswith("/"):
        return ("MISSING_BASE", target) if BASE else DIST / t.lstrip("/")
    p = DIST / t.lstrip("/")
    if p.is_dir():
        p = p / "index.html"
    elif t.endswith("/"):
        p = p / "index.html"
    return p


def main() -> int:
    if not DIST.exists():
        sys.stderr.write("dist/ does not exist — run build.py first.\n")
        return 2

    pages = sorted(DIST.rglob("*.html"))
    problems: list[str] = []
    checked = 0

    for page in pages:
        rel = page.relative_to(DIST)
        html = page.read_text(encoding="utf-8")
        parser = Links()
        parser.feed(html)

        for href in parser.hrefs + parser.srcs:
            target = resolve(href)
            if target is None:
                continue
            checked += 1
            if isinstance(target, tuple):
                problems.append(f"{rel}: link missing the {BASE} base path → {href}")
            elif not target.exists():
                problems.append(f"{rel}: broken link → {href}")

        if not parser.has_h1:
            problems.append(f"{rel}: no <h1>")
        if not parser.title.strip():
            problems.append(f"{rel}: empty <title>")
        if parser.imgs_without_alt:
            problems.append(f"{rel}: {parser.imgs_without_alt} image(s) without alt text")
        if not re.search(r'name="description"\s+content="[^"]{20,}"', html):
            problems.append(f"{rel}: missing or thin meta description")
        if not re.search(r'rel="canonical"', html):
            problems.append(f"{rel}: no canonical URL")
        for needed in REQUIRED_FOOTER_LINKS:
            if f'href="{needed}"' not in html:
                problems.append(f"{rel}: footer is missing a link to {needed}")

    # Site-level requirements
    for f in ("robots.txt", "sitemap.xml", "feed.xml", "ads.txt", "404.html",
              "privacy/index.html", "terms/index.html", "about/index.html",
              "contact/index.html", "editorial-standards/index.html",
              "corrections/index.html", "cookies/index.html"):
        if not (DIST / f).exists():
            problems.append(f"site: missing required file {f}")

    print(f"Checked {len(pages)} page(s), {checked} internal link(s).")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("All internal links resolve. All required pages and metadata present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
