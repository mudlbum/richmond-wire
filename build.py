#!/usr/bin/env python3
"""
Richmond International Newswire — static site generator.

Reads JSON article files from content/<YYYY-MM-DD>/ and renders a complete
static site into dist/. Standard library only, no dependencies.

    python3 build.py            # build everything
    python3 build.py --serve    # build, then serve dist/ on :8000
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sys
import xml.sax.saxutils as sx
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
PAGES = ROOT / "pages"
STATIC = ROOT / "static"
DIST = ROOT / "dist"

E = html.escape


# --------------------------------------------------------------------------
# config + helpers
# --------------------------------------------------------------------------
def load_site() -> dict:
    with open(ROOT / "site.json", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["url"] = cfg["url"].rstrip("/")

    # GitHub Pages serves a project repo under /<repo>/, so every root-relative
    # link needs that prefix. Derive it from the configured URL rather than making
    # it a second setting that can drift out of sync with it.
    m = re.match(r"^(https?://[^/]+)(/.*)?$", cfg["url"])
    if not m:
        raise SystemExit(f"site.url is not a valid absolute URL: {cfg['url']!r}")
    cfg["origin"] = m.group(1)
    cfg["base_path"] = (m.group(2) or "").rstrip("/")

    cfg["cat_by_slug"] = {c["slug"]: c for c in cfg["categories"]}

    # Placeholders are fine while you are building. They are not fine once ads are
    # live: a wrong canonical URL breaks SEO silently, and Google checks that the
    # contact route on your site actually works.
    if cfg["adsense"].get("enabled"):
        bad = []
        if "example." in cfg["url"]:
            bad.append(f"site.url is still a placeholder ({cfg['url']})")
        if "X" in cfg["adsense"].get("publisher_id", ""):
            bad.append("adsense.publisher_id is still a placeholder")
        for k, v in cfg["publisher"].items():
            if isinstance(v, str) and "example.com" in v:
                bad.append(f"publisher.{k} is still a placeholder ({v})")
        if bad:
            raise SystemExit("Refusing to build with ads enabled:\n  - "
                             + "\n  - ".join(bad))
    return cfg


def slugify(text: str, maxlen: int = 70) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:maxlen].rstrip("-") or "story"


def parse_date(d: str) -> dt.date:
    return dt.date.fromisoformat(d)


def long_date(d: dt.date) -> str:
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


def short_date(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def rfc822(d: dt.date, hour: int = 12) -> str:
    stamp = dt.datetime(d.year, d.month, d.day, hour, 0, 0, tzinfo=dt.timezone.utc)
    return stamp.strftime("%a, %d %b %Y %H:%M:%S +0000")


def iso_stamp(d: dt.date, hour: int = 12) -> str:
    return dt.datetime(d.year, d.month, d.day, hour, 0, 0,
                       tzinfo=dt.timezone.utc).isoformat()


def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).replace("www.", "") if m else ""


_BASE_PATH = ""


def apply_base_path(html: str) -> str:
    """Prefix every root-relative href/src with the site's base path.

    Only strings starting with a single "/" are touched: absolute URLs, protocol-
    relative URLs, mailto:, and #anchors all start with something else and are left
    exactly as they are."""
    if not _BASE_PATH:
        return html
    for attr in ("href", "src"):
        html = re.sub(rf'{attr}="/(?!/)', f'{attr}="{_BASE_PATH}/', html)
    return html


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".html":
        text = apply_base_path(text)
    path.write_text(text, encoding="utf-8")


# Hand-picked so section cards read as a deliberate palette rather than as
# whatever a hash happened to produce. Each entry is (hue, saturation).
SECTION_HUE = {
    "world":       (208, 34),
    "economy":     (172, 30),
    "technology":  (255, 28),
    "science":     (280, 26),
    "sport":       (140, 30),
    "culture":     (338, 30),
    "environment": (100, 30),
    "society":     (28, 34),
}


def gradient_for(seed: str, category: str = "") -> tuple[str, str]:
    """Deterministic gradient used when no photograph is available.

    Hue comes from the section so a category page looks coherent; the small
    per-article shift keeps ten cards from looking like one repeated tile."""
    base, sat = SECTION_HUE.get(category, (210, 30))
    shift = int(hashlib.sha256(seed.encode()).hexdigest()[:4], 16) % 22 - 11
    hue = (base + shift) % 360
    return (f"hsl({hue},{sat}%,34%)", f"hsl({(hue + 26) % 360},{sat + 6}%,17%)")


# --------------------------------------------------------------------------
# content loading
# --------------------------------------------------------------------------
REQUIRED = ("headline", "dek", "summary", "category", "body", "sources")


def load_edition_meta(day_dir: Path) -> dict:
    """Read a day's edition.json, which records whether an editor approved it.

    Missing or unreadable metadata is treated as UNREVIEWED, never as approved --
    the site must never claim review it cannot evidence."""
    f = day_dir / "edition.json"
    if not f.exists():
        return {"review": {"status": "unreviewed"}}
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.stderr.write(f"  ! {f} is unreadable; treating edition as unreviewed\n")
        return {"review": {"status": "unreviewed"}}
    review = meta.get("review") or {}
    if review.get("status") not in ("approved", "auto", "unreviewed", "pending"):
        review["status"] = "unreviewed"
    # "pending" means the gate never resolved. Nothing on a live site is pending.
    if review["status"] == "pending":
        review["status"] = "unreviewed"
    meta["review"] = review
    return meta


def order_key(a: dict) -> str:
    """Where an article sits within its day. Newest first.

    The wire files stories every two hours, so within one day the interesting
    order is when each was published, not which beat produced it. Stories carry
    `published_at`; anything older than that field sorts underneath them, by
    rank, so the editions that pre-date the two-hourly wire still read in the
    order they were written. A sortable string keeps both in one key, and "!"
    sorts below the "2" that begins every ISO timestamp."""
    when = str(a.get("published_at") or "")
    if when:
        return when
    return f"!{max(0, 99 - int(a.get('rank', 99))):02d}"


def load_articles(site: dict) -> list[dict]:
    articles: list[dict] = []
    if not CONTENT.exists():
        return articles

    for day_dir in sorted(CONTENT.iterdir()):
        if not day_dir.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_dir.name):
            continue
        edition_meta = load_edition_meta(day_dir)
        for jf in sorted(day_dir.glob("*.json")):
            if jf.name == "edition.json":
                continue
            with open(jf, encoding="utf-8") as f:
                a = json.load(f)

            missing = [k for k in REQUIRED if not a.get(k)]
            if missing:
                sys.stderr.write(f"  ! skipping {jf.name}: missing {missing}\n")
                continue
            if a["category"] not in site["cat_by_slug"]:
                sys.stderr.write(f"  ! skipping {jf.name}: unknown category "
                                 f"{a['category']!r}\n")
                continue

            a["date"] = a.get("date") or day_dir.name
            d = parse_date(a["date"])
            a["_date"] = d
            a["slug"] = a.get("slug") or slugify(a["headline"])
            a["path"] = f"/{d.year}/{d.month:02d}/{d.day:02d}/{a['slug']}/"
            a["rank"] = int(a.get("rank", 99))
            a["_order"] = order_key(a)
            a["_review"] = edition_meta["review"]
            a["read_minutes"] = a.get("read_minutes") or estimate_reading(a)
            articles.append(a)

    articles.sort(key=lambda x: (x["_date"], x["_order"]), reverse=True)
    return articles


def estimate_reading(a: dict) -> int:
    words = len(a.get("dek", "").split())
    for b in a.get("body", []):
        words += len(b.get("text", "").split())
        for item in b.get("items", []):
            words += len(str(item).split())
    return max(1, round(words / 220))


def link_follow_ups(articles: list[dict]) -> None:
    """Point each follow-up at the article it continues.

    An unresolvable slug is dropped rather than rendered as a dead link: a
    'previously' box that goes nowhere is worse than none."""
    by_slug = {a["slug"]: a for a in articles}
    for a in articles:
        fu = a.get("follow_up") or {}
        prior = by_slug.get(fu.get("of", ""))
        if prior and prior is not a:
            fu["_path"] = prior["path"]
            fu["_headline"] = prior["headline"]
            fu["_date"] = short_date(prior["_date"])
        elif fu:
            sys.stderr.write(f"  ! {a['slug']}: follow_up points at unknown slug "
                             f"{fu.get('of','')!r}; rendering without a link\n")


def editions_of(articles: list[dict]) -> dict[dt.date, list[dict]]:
    by_day: dict[dt.date, list[dict]] = {}
    for a in articles:
        by_day.setdefault(a["_date"], []).append(a)
    for day in by_day:
        by_day[day].sort(key=lambda x: x["_order"], reverse=True)
    return dict(sorted(by_day.items(), reverse=True))


# --------------------------------------------------------------------------
# chrome: head, masthead, footer
# --------------------------------------------------------------------------
NAV = [("/", "Today"), ("/world/", "World"), ("/economy/", "Economy"),
       ("/technology/", "Technology"), ("/science/", "Science"),
       ("/sport/", "Sport"), ("/culture/", "Culture"),
       ("/environment/", "Environment"), ("/society/", "Society"),
       ("/archive/", "Archive")]


def review_status(a: dict) -> str:
    """approved = an editor read it and released it. auto = the review window
    expired and it published unread. unreviewed = no gate, or no evidence."""
    return (a.get("_review") or {}).get("status", "unreviewed")


def reviewer_name(site: dict) -> str:
    return site.get("editorial", {}).get("reviewer_credit") or "the editor"


def review_label(site: dict, a: dict) -> str:
    """The short line in the article meta bar. One of these is true per article;
    none of them is a blanket claim about the site."""
    st = review_status(a)
    if st == "approved":
        return f"AI-drafted, read and approved by {reviewer_name(site)} before publication"
    if st == "auto":
        return "AI-drafted — published on the review timer, NOT read by an editor"
    return "AI-drafted, automatically verified — not read by a human before publishing"


def review_sentence_for(site: dict, a: dict) -> str:
    st = review_status(a)
    rev = (a.get("_review") or {})
    if st == "approved":
        when = rev.get("reviewed_at", "")
        stamp = f" ({E(when)})" if when else ""
        return (f"<strong>{E(reviewer_name(site)).capitalize()} read this article and "
                f"released it for publication{stamp}.</strong>")
    if st == "auto":
        hold = site.get("editorial", {}).get("hold_hours", 12)
        return (f"<strong>No editor read this article before it went live.</strong> It was "
                f"held for {hold} hours for review, the window expired without anyone "
                f"releasing it, and it published automatically. Our "
                f'<a href="/editorial-standards/">editorial standards</a> explain why we '
                f"label this rather than hide it.")
    return "<strong>No human read this article before it went live.</strong>"


def adsense_head(site: dict) -> str:
    """Emit ad configuration, never the ad loader itself.

    The AdSense script is injected by static/consent.js only after consent, or --
    when consent_mode is "google_cmp" -- immediately, because Google's own
    certified CMP is then responsible for gating personalisation. Loading
    adsbygoogle.js unconditionally from here would set advertising cookies before
    the visitor answered the banner, which breaks Google's EU user consent policy
    and makes our own cookie policy untrue."""
    ad = site["adsense"]
    if not ad.get("enabled"):
        return ""
    mode = ad.get("consent_mode", "self")
    cfg = json.dumps({"client": ad["publisher_id"], "mode": mode})
    tag = f'\n<script>window.__RIN_ADS={cfg};</script>'
    if mode == "google_cmp":
        # Google's CMP is loaded with the AdSense tag and handles consent itself.
        tag += (f'\n<script async src="https://pagead2.googlesyndication.com/pagead/js/'
                f'adsbygoogle.js?client={E(ad["publisher_id"])}" crossorigin="anonymous">'
                f'</script>')
    return tag


def ad_slot(site: dict, kind: str, label: str = "Advertisement") -> str:
    ad = site["adsense"]
    if not ad.get("enabled"):
        return (f'<div class="adslot"><p class="adslot-label">{E(label)}</p>'
                f'<div class="adslot-box">Ad space — {E(kind)}</div></div>')
    slot = ad["slots"].get(kind, "")
    # No inline push here -- consent.js pushes every slot once the loader is in.
    return (f'<div class="adslot"><p class="adslot-label">{E(label)}</p>'
            f'<ins class="adsbygoogle" style="display:block" '
            f'data-ad-client="{E(ad["publisher_id"])}" data-ad-slot="{E(slot)}" '
            f'data-ad-format="auto" data-full-width-responsive="true"></ins></div>')


def head(site: dict, *, title: str, desc: str, canonical: str,
         extra: str = "", jsonld: list | None = None,
         og_type: str = "website", og_image: str = "") -> str:
    full_title = title if title == site["name"] else f"{title} — {site['short_name']}"
    ld = ""
    for block in (jsonld or []):
        ld += ('\n<script type="application/ld+json">'
               + json.dumps(block, ensure_ascii=False) + "</script>")
    img = f'\n<meta property="og:image" content="{E(og_image)}">' if og_image else ""
    return f"""<!doctype html>
<html lang="{E(site['locale'])}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(full_title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="canonical" href="{E(site['url'] + canonical)}">
<meta name="robots" content="max-image-preview:large">
<meta property="og:site_name" content="{E(site['name'])}">
<meta property="og:type" content="{E(og_type)}">
<meta property="og:title" content="{E(title)}">
<meta property="og:description" content="{E(desc)}">
<meta property="og:url" content="{E(site['url'] + canonical)}">{img}
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" type="application/rss+xml" title="{E(site['name'])}" href="/feed.xml">
<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">{adsense_head(site)}{ld}{extra}
</head>
<body>
<a class="skip" href="#main">Skip to main content</a>"""


def masthead(site: dict, current: str = "") -> str:
    today = dt.date.today()
    items = "".join(
        f'<li><a href="{E(h)}"'
        + (' aria-current="page"' if h == current else "")
        + f">{E(label)}</a></li>"
        for h, label in NAV)
    return f"""
<header class="masthead">
  <div class="wrap">
    <div class="masthead-top">
      <span>{E(long_date(today))}</span>
      <span class="mh-right">Independent &middot; Non-partisan &middot; Sourced</span>
    </div>
    <div class="masthead-main">
      <a class="flag" href="/">{E(site['name'])}</a>
      <div class="flag-rule" aria-hidden="true"></div>
      <p class="tagline">{E(site['tagline'])}</p>
    </div>
  </div>
</header>
<nav class="primary" aria-label="Sections"><div class="wrap"><ul>{items}</ul></div></nav>
<main id="main">"""


def footer(site: dict) -> str:
    pub = site["publisher"]
    mode = site.get("editorial", {}).get("review_mode", "publish_immediately")
    hold = site.get("editorial", {}).get("hold_hours", 12)
    if mode == "hold_until_approved":
        foot_review = ("Articles are researched and written by an AI system. No article is "
                       "published until an editor has read and released it.")
    elif mode == "hold_then_publish":
        foot_review = (f"Articles are researched and written by an AI system, then held for "
                       f"{hold} hours so an editor can read them. Most are released by a "
                       f"person; when the window expires an edition publishes unread, and "
                       f"<strong>every article says which of the two happened to it</strong>.")
    else:
        foot_review = ("Articles are researched, written and verified automatically by an AI "
                       "system, without a human reading them first.")
    year = dt.date.today().year
    cats = "".join(f'<li><a href="/{E(c["slug"])}/">{E(c["name"])}</a></li>'
                   for c in site["categories"])
    return f"""</main>
<footer class="site">
  <div class="wrap">
    <div class="footcols">
      <div>
        <h2>Sections</h2>
        <ul>{cats}</ul>
      </div>
      <div>
        <h2>The Newswire</h2>
        <ul>
          <li><a href="/about/">About us</a></li>
          <li><a href="/editorial-standards/">Editorial standards &amp; AI disclosure</a></li>
          <li><a href="/corrections/">Corrections</a></li>
          <li><a href="/contact/">Contact</a></li>
        </ul>
      </div>
      <div>
        <h2>Legal</h2>
        <ul>
          <li><a href="/privacy/">Privacy policy</a></li>
          <li><a href="/cookies/">Cookie policy</a></li>
          <li><a href="/terms/">Terms of use</a></li>
          <li><a href="/privacy/#your-choices">Your privacy choices</a></li>
        </ul>
      </div>
      <div>
        <h2>Follow</h2>
        <ul>
          <li><a href="/feed.xml">RSS feed</a></li>
          <li><a href="/archive/">Full archive</a></li>
          <li><a href="/sitemap.xml">Sitemap</a></li>
        </ul>
      </div>
    </div>
    <div class="colophon">
      <p><strong>{E(site['name'])}</strong> is an independent daily digest published from
      {E(pub['location'])}. We summarise reporting from established news organisations,
      name every source, and link to the original coverage. We are not a wire service and
      we do not employ correspondents in the field.</p>
      <p>{foot_review}
      See our <a href="/editorial-standards/">editorial standards and AI disclosure</a> for
      exactly what that means and what checks run, and our
      <a href="/corrections/">corrections policy</a> if you spot an error.</p>
      <p>&copy; {year} {E(pub['legal_name'])}. Original text is our own; quotations and facts
      remain the property of the organisations credited in each article.</p>
    </div>
  </div>
</footer>
<div id="consent" hidden>
  <div class="inner">
    <p><strong>Cookies.</strong> We use a strictly necessary cookie to remember this choice.
    If you accept, we also allow advertising partners (including Google) to set cookies to
    personalise ads and measure performance. See our
    <a href="/cookies/">cookie policy</a> and <a href="/privacy/">privacy policy</a>.</p>
    <div class="btns">
      <button type="button" data-consent="reject">Reject non-essential</button>
      <button type="button" class="primary" data-consent="accept">Accept all</button>
    </div>
  </div>
</div>
<script src="/static/consent.js" defer></script>
</body>
</html>"""


# --------------------------------------------------------------------------
# image rendering
# --------------------------------------------------------------------------
STOCK_NOTE = ("Illustrative stock photograph. It was not taken at the event described "
              "in this article and does not depict any person involved in it.")


def render_figure(a: dict, *, lazy: bool = False) -> str:
    img = a.get("image") or {}
    alt = img.get("alt") or a["headline"]
    loading = ' loading="lazy" decoding="async"' if lazy else ""

    if img.get("url"):
        credit_name = img.get("photographer") or "Unknown"
        credit_url = img.get("photographer_url") or "https://www.pexels.com"
        credit = (f'<span class="imgcredit">Photo: '
                  f'<a href="{E(credit_url)}" rel="nofollow noopener" target="_blank">'
                  f'{E(credit_name)}</a> via '
                  f'<a href="https://www.pexels.com" rel="nofollow noopener" '
                  f'target="_blank">Pexels</a>, used under the Pexels licence.</span>')
        media = (f'<img src="{E(img["url"])}" alt="{E(alt)}" width="1200" height="675"'
                 f'{loading}>')
    else:
        c1, c2 = gradient_for(a["slug"], a["category"])
        cat = a["category"].upper()
        credit = ('<span class="imgcredit">No photograph is used with this article. '
                  'The graphic above is generated from the article\'s section colour.</span>')
        media = (f'<div class="placeholder-art" role="img" aria-label="Decorative '
                 f'graphic for the {E(a["category"])} section" '
                 f'style="--c1:{c1};--c2:{c2}"><span>{E(cat)}</span></div>')

    note = (f'<span class="imgnote">{E(STOCK_NOTE)}</span>' if img.get("url") else "")
    return (f'<figure>{media}<figcaption>{note}{credit}</figcaption></figure>')


def thumb(a: dict) -> str:
    img = a.get("image") or {}
    alt = img.get("alt") or a["headline"]
    if img.get("thumb") or img.get("url"):
        src = img.get("thumb") or img["url"]
        return (f'<a class="thumb" href="{E(a["path"])}" tabindex="-1" aria-hidden="true">'
                f'<img src="{E(src)}" alt="{E(alt)}" loading="lazy" decoding="async" '
                f'width="600" height="338"></a>')
    c1, c2 = gradient_for(a["slug"], a["category"])
    # No label here: the section tag already sits directly beneath the card image.
    return (f'<a class="thumb" href="{E(a["path"])}" tabindex="-1" aria-hidden="true">'
            f'<div class="placeholder-art" style="--c1:{c1};--c2:{c2}"></div></a>')


# --------------------------------------------------------------------------
# article body rendering
# --------------------------------------------------------------------------
def inline(text: str) -> str:
    """Escape, then re-enable a tiny markdown subset: **bold**, *italic*, [x](url)."""
    out = E(text)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                 r'<a href="\2" rel="nofollow noopener" target="_blank">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def render_body(blocks: list[dict], site: dict, inject_ad_after: int = 3) -> str:
    out: list[str] = []
    paragraphs = 0
    ad_done = False
    for b in blocks:
        kind = b.get("type", "p")
        if kind == "p":
            out.append(f"<p>{inline(b['text'])}</p>")
            paragraphs += 1
            if paragraphs == inject_ad_after and not ad_done:
                out.append(ad_slot(site, "in_article"))
                ad_done = True
        elif kind == "h2":
            out.append(f"<h2>{inline(b['text'])}</h2>")
        elif kind in ("ul", "ol"):
            tag = kind
            lis = "".join(f"<li>{inline(str(i))}</li>" for i in b.get("items", []))
            out.append(f"<{tag}>{lis}</{tag}>")
        elif kind == "quote":
            cite = (f"<cite>{inline(b['cite'])}</cite>" if b.get("cite") else "")
            out.append(f"<blockquote><p>{inline(b['text'])}</p>{cite}</blockquote>")
        elif kind == "box":
            title = f"<h3>{inline(b['title'])}</h3>" if b.get("title") else ""
            inner = ""
            if b.get("text"):
                inner += f"<p>{inline(b['text'])}</p>"
            if b.get("items"):
                inner += "<ul>" + "".join(
                    f"<li>{inline(str(i))}</li>" for i in b["items"]) + "</ul>"
            cls = "box box--warn" if b.get("variant") == "warn" else "box"
            out.append(f'<div class="{cls}">{title}{inner}</div>')
    return "\n".join(out)


def render_follow_up(a: dict) -> str:
    """Say plainly that this continues an earlier story, and what changed.

    The newswire's duplicate check lets a repeat story through only when it
    declares this. Showing the declaration to readers is what keeps that from
    being a private formality."""
    fu = a.get("follow_up") or {}
    whats_new = fu.get("whats_new", "")
    if not whats_new:
        return ""
    if fu.get("_path"):
        link = (f'<a href="{E(fu["_path"])}">{E(fu["_headline"])}</a>'
                f' ({E(fu.get("_date", ""))})')
    else:
        link = "an earlier edition"
    return (f'<div class="box box--followup"><h3>This continues an earlier story</h3>'
            f'<p>We previously reported {link}. '
            f'<strong>What has changed since:</strong> {inline(whats_new)}</p></div>')


def render_sources(a: dict) -> str:
    items = ""
    for s in a["sources"]:
        outlet = E(s.get("outlet", domain_of(s.get("url", ""))))
        title = E(s.get("title", ""))
        url = E(s.get("url", ""))
        note = f' <em>({E(s["note"])})</em>' if s.get("note") else ""
        items += (f'<li><strong>{outlet}</strong> — '
                  f'<a href="{url}" rel="nofollow noopener" target="_blank">{title}</a>'
                  f'{note}</li>')
    return (f'<div class="box box--sources"><h3>Sources for this article</h3>'
            f'<ol>{items}</ol></div>')


def render_uncertain(a: dict) -> str:
    if not a.get("uncertain"):
        return ""
    lis = "".join(f"<li>{inline(str(u))}</li>" for u in a["uncertain"])
    return (f'<div class="box box--warn uncertain"><h3>What is not yet confirmed</h3>'
            f"<ul>{lis}</ul></div>")


# --------------------------------------------------------------------------
# page builders
# --------------------------------------------------------------------------
def build_article(site: dict, a: dict, edition: list[dict]) -> None:
    d = a["_date"]
    cat = site["cat_by_slug"][a["category"]]
    canonical = a["path"]
    og_img = (a.get("image") or {}).get("url", "")

    ld_article = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": a["headline"][:110],
        "description": a["summary"],
        "datePublished": iso_stamp(d),
        "dateModified": a.get("updated") or iso_stamp(d),
        "articleSection": cat["name"],
        "inLanguage": site["locale"],
        "mainEntityOfPage": {"@type": "WebPage", "@id": site["url"] + canonical},
        "publisher": {
            "@type": "NewsMediaOrganization",
            "name": site["publisher"]["name"],
            "url": site["url"],
        },
        "author": {"@type": "Organization", "name": site["publisher"]["name"],
                   "url": site["url"] + "/about/"},
        "isBasedOn": [s["url"] for s in a["sources"] if s.get("url")],
    }
    if og_img:
        ld_article["image"] = [og_img]

    ld_crumbs = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": site["url"] + "/"},
            {"@type": "ListItem", "position": 2, "name": cat["name"],
             "item": f"{site['url']}/{cat['slug']}/"},
            {"@type": "ListItem", "position": 3, "name": a["headline"]},
        ],
    }

    tags = "".join(f'<span>{E(t)}</span>' for t in a.get("tags", [])[:5])
    related = [x for x in edition if x["slug"] != a["slug"]][:4]
    rel_html = ""
    if related:
        rel_items = "".join(
            f'<li><a href="{E(r["path"])}">{E(r["headline"])}</a></li>'
            for r in related)
        rel_html = (f'<div class="box"><h3>Also in this edition</h3>'
                    f"<ul>{rel_items}</ul></div>")

    review_sentence = review_sentence_for(site, a)
    review_class = (' class="review-flag"' if review_status(a) in ("auto", "unreviewed")
                    else ' class="review-ok"')
    doc = head(site, title=a["headline"], desc=a["summary"], canonical=canonical,
               jsonld=[ld_article, ld_crumbs], og_type="article", og_image=og_img)
    doc += masthead(site, f"/{cat['slug']}/")
    doc += f"""
<div class="wrap">
<article class="article">
  <div class="article-head">
    <p class="kicker"><a class="card-tag" href="/{E(cat['slug'])}/">{E(cat['name'])}</a></p>
    <h1>{E(a['headline'])}</h1>
    <p class="dek">{E(a['dek'])}</p>
    <div class="article-meta">
      <span>Published <time datetime="{E(d.isoformat())}">{E(long_date(d))}</time></span>
      <span>&middot;</span>
      <span>{a['read_minutes']} min read</span>
      <span>&middot;</span>
      <span>Compiled from {len(a['sources'])} sources</span>
      <span>&middot;</span>
      <span{review_class}>{E(review_label(site, a))}</span>
    </div>
  </div>
  {render_figure(a)}
  <div class="article-body">
    {render_follow_up(a)}
    {render_body(a['body'], site)}
    {render_uncertain(a)}
    {render_sources(a)}
    {rel_html}
  </div>
  <p class="ai-notice">
    <strong>How this article was made.</strong> It is an original summary of reporting by the
    news organisations listed above. An AI system researched it, drafted it, and checked it
    against automated verification rules — every source link is fetched and every claim must
    trace to a named source, or the article does not publish. {review_sentence}
    We did not witness these events. Figures from a developing story can change after
    publication — follow the source links for the latest. Found a mistake?
    <a href="/corrections/">Tell us</a> and a person will fix it in public.
  </p>
</article>
</div>
{ad_slot(site, 'leaderboard')}
"""
    doc += footer(site)
    write(DIST / canonical.strip("/") / "index.html", doc)


def story_card(a: dict, site: dict, *, lede: bool = False) -> str:
    cat = site["cat_by_slug"][a["category"]]
    h = "h2" if lede else "h3"
    return f"""<article class="card">
  {thumb(a)}
  <a class="card-tag" href="/{E(cat['slug'])}/">{E(cat['name'])}</a>
  <{h}><a href="{E(a['path'])}">{E(a['headline'])}</a></{h}>
  <p>{E(a['summary'])}</p>
  <p class="byline">{E(short_date(a['_date']))} &middot; {a['read_minutes']} min read
  &middot; {len(a['sources'])} sources</p>
</article>"""


def edition_banner(site: dict, arts: list[dict]) -> str:
    if not arts:
        return ""
    st = review_status(arts[0])
    if st == "approved":
        rev = (arts[0].get("_review") or {})
        when = rev.get("reviewed_at", "")
        stamp = f" on {E(when)}" if when else ""
        return (f'<div class="box editionflag editionflag--ok"><h3>Reviewed edition</h3>'
                f'<p>{E(reviewer_name(site)).capitalize()} read every story below and '
                f'released this edition{stamp}. '
                f'<a href="/editorial-standards/">How we review</a>.</p></div>')
    hold = site.get("editorial", {}).get("hold_hours", 12)
    if st == "auto":
        return (f'<div class="box box--warn editionflag"><h3>Unreviewed edition</h3>'
                f'<p>These stories were held {hold} hours for editorial review and no one '
                f'released them, so they published automatically. They passed our automated '
                f'source checks, but <strong>no person read them before publication</strong>. '
                f'<a href="/editorial-standards/">What that means</a>.</p></div>')
    return (f'<div class="box box--warn editionflag"><h3>Unreviewed edition</h3>'
            f'<p>No person read these stories before publication. They passed our automated '
            f'source and verification checks only. '
            f'<a href="/editorial-standards/">What that means</a>.</p></div>')


def edition_block(site: dict, day: dt.date, arts: list[dict], *, heading: str,
                  dek: str) -> str:
    lede = arts[0]
    rest = arts[1:]
    second = rest[0] if rest else None
    grid = "".join(story_card(a, site) for a in rest[1:])
    aside = story_card(second, site) if second else ""
    return f"""
<div class="edition-head">
  <p class="kicker">Edition of {E(long_date(day))}</p>
  <h1>{E(heading)}</h1>
  <p class="dek">{E(dek)}</p>
</div>
{edition_banner(site, arts)}
<div class="lede">
  <div>{story_card(lede, site, lede=True)}</div>
  <div>{aside}</div>
</div>
{ad_slot(site, 'leaderboard')}
<div class="stories">{grid}</div>
"""


def build_home(site: dict, by_day: dict, articles: list[dict]) -> None:
    day, arts = next(iter(by_day.items()))
    ld = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": site["name"],
        "url": site["url"],
        "description": site["description"],
        "publisher": {"@type": "NewsMediaOrganization", "name": site["publisher"]["name"],
                      "url": site["url"]},
    }
    doc = head(site, title=site["name"], desc=site["description"], canonical="/",
               jsonld=[ld])
    doc += masthead(site, "/")
    doc += '<div class="wrap">'
    doc += edition_block(site, day, arts,
                         heading="Today's ten stories",
                         dek=("Ten things that happened in the world, each checked against "
                              "multiple independent news organisations and linked back to "
                              "the original reporting."))
    prev_days = list(by_day.items())[1:6]
    if prev_days:
        items = "".join(
            f'<li><span class="d">{E(long_date(d))}</span>'
            f'<a href="/{d.year}/{d.month:02d}/{d.day:02d}/">'
            f'{len(v)} stories — {E(v[0]["headline"])}</a></li>'
            for d, v in prev_days)
        doc += (f'<div class="box"><h3>Earlier editions</h3>'
                f'<ul class="archive-list">{items}</ul>'
                f'<p style="margin-top:14px"><a href="/archive/">Browse the full archive '
                f'&rarr;</a></p></div>')
    doc += "</div>"
    doc += footer(site)
    write(DIST / "index.html", doc)


def build_edition_pages(site: dict, by_day: dict) -> None:
    for day, arts in by_day.items():
        path = f"/{day.year}/{day.month:02d}/{day.day:02d}/"
        doc = head(site, title=f"Edition of {long_date(day)}",
                   desc=f"All {len(arts)} stories published by {site['name']} on "
                        f"{long_date(day)}.",
                   canonical=path)
        doc += masthead(site)
        doc += '<div class="wrap">'
        doc += edition_block(site, day, arts,
                             heading=f"Edition of {long_date(day)}",
                             dek=f"The {len(arts)} stories we published that day, in the "
                                 f"order they ran.")
        doc += "</div>"
        doc += footer(site)
        write(DIST / path.strip("/") / "index.html", doc)


def build_categories(site: dict, articles: list[dict]) -> None:
    for cat in site["categories"]:
        arts = [a for a in articles if a["category"] == cat["slug"]]
        path = f"/{cat['slug']}/"
        doc = head(site, title=cat["name"],
                   desc=f"{cat['blurb']} Every {cat['name'].lower()} story from "
                        f"{site['name']}, newest first.",
                   canonical=path)
        doc += masthead(site, path)
        doc += f"""<div class="wrap">
<div class="edition-head">
  <p class="kicker">Section</p>
  <h1>{E(cat['name'])}</h1>
  <p class="dek">{E(cat['blurb'])}</p>
</div>"""
        if arts:
            doc += '<div class="stories">' + "".join(
                story_card(a, site) for a in arts) + "</div>"
        else:
            doc += ('<p>No stories in this section yet. The newswire publishes ten stories '
                    'a day across eight sections — check back tomorrow.</p>')
        doc += "</div>"
        doc += footer(site)
        write(DIST / cat["slug"] / "index.html", doc)


def build_archive(site: dict, by_day: dict) -> None:
    rows = ""
    for day, arts in by_day.items():
        links = "".join(
            f'<li><span class="d">{E(site["cat_by_slug"][a["category"]]["name"])}</span>'
            f'<a href="{E(a["path"])}">{E(a["headline"])}</a></li>' for a in arts)
        rows += (f'<h2><a href="/{day.year}/{day.month:02d}/{day.day:02d}/">'
                 f'{E(long_date(day))}</a></h2>'
                 f'<ul class="archive-list">{links}</ul>')
    doc = head(site, title="Archive",
               desc=f"Every story {site['name']} has published, organised by edition date.",
               canonical="/archive/")
    doc += masthead(site, "/archive/")
    doc += f"""<div class="wrap"><div class="page">
<div class="edition-head" style="border:none;padding-top:34px">
  <p class="kicker">Archive</p>
  <h1>Every edition</h1>
  <p class="dek">Ten stories a day, kept permanently and never quietly edited. Changes to a
  published article are logged on our <a href="/corrections/">corrections page</a>.</p>
</div>
{rows}
</div></div>"""
    doc += footer(site)
    write(DIST / "archive" / "index.html", doc)


# --------------------------------------------------------------------------
# static pages (markdown-lite -> html)
# --------------------------------------------------------------------------
def md_lite(text: str) -> str:
    """Very small markdown subset: ##/### headings, -, 1., blank-line paragraphs."""
    out, buf, mode = [], [], None

    def flush():
        nonlocal buf, mode
        if not buf:
            return
        if mode == "ul":
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in buf) + "</ul>")
        elif mode == "ol":
            out.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in buf) + "</ol>")
        else:
            out.append(f"<p>{inline(' '.join(buf))}</p>")
        buf, mode = [], None

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        i += 1
        if not line.strip():
            flush()
            continue
        # pipe tables
        if line.lstrip().startswith("|") and i < len(lines) and \
                re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i]):
            flush()
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            head_html = "".join(f"<th>{inline(c)}</th>" for c in cells)
            i += 1
            rows = ""
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rc = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in rc) + "</tr>"
                i += 1
            out.append(f'<div class="tablewrap"><table><thead><tr>{head_html}</tr>'
                       f"</thead><tbody>{rows}</tbody></table></div>")
            continue
        if line.startswith("### ") or line.startswith("## "):
            flush()
            lvl = 3 if line.startswith("### ") else 2
            txt = line[lvl + 1:]
            anchor = ""
            m = re.search(r"\s*\{#([\w-]+)\}\s*$", txt)
            if m:
                anchor = f' id="{E(m.group(1))}"'
                txt = txt[: m.start()]
            out.append(f"<h{lvl}{anchor}>{inline(txt)}</h{lvl}>")
            continue
        if line.startswith("<"):
            flush(); out.append(line); continue
        m = re.match(r"^[-*]\s+(.*)", line)
        if m:
            if mode != "ul":
                flush(); mode = "ul"
            buf.append(m.group(1)); continue
        m = re.match(r"^\d+\.\s+(.*)", line)
        if m:
            if mode != "ol":
                flush(); mode = "ol"
            buf.append(m.group(1)); continue
        if mode in ("ul", "ol"):
            flush()
        mode = "p"
        buf.append(line.strip())
    flush()
    return "\n".join(out)


def build_static_pages(site: dict) -> None:
    if not PAGES.exists():
        return
    for mf in sorted(PAGES.glob("*.md")):
        raw = mf.read_text(encoding="utf-8")
        meta: dict = {}
        if raw.startswith("---"):
            _, fm, raw = raw.split("---", 2)
            for line in fm.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
        slug = meta.get("slug", mf.stem)
        path = f"/{slug}/"
        subs = {"{{SITE}}": site["name"], "{{EMAIL}}": site["publisher"]["email"],
                "{{CORRECTIONS_EMAIL}}": site["publisher"]["corrections_email"],
                "{{PRIVACY_EMAIL}}": site["publisher"]["privacy_email"],
                "{{LOCATION}}": site["publisher"]["location"],
                "{{URL}}": site["url"], "{{LEGAL}}": site["publisher"]["legal_name"]}
        for k, v in subs.items():
            raw = raw.replace(k, v)
        updated = meta.get("updated", dt.date.today().isoformat())
        doc = head(site, title=meta.get("title", slug.title()),
                   desc=meta.get("description", site["description"]), canonical=path)
        doc += masthead(site)
        doc += f"""<div class="wrap"><div class="page">
<h1>{E(meta.get('title', slug.title()))}</h1>
<p class="updated">Last updated {E(long_date(parse_date(updated)))}</p>
{md_lite(raw)}
</div></div>"""
        doc += footer(site)
        write(DIST / slug / "index.html", doc)


# --------------------------------------------------------------------------
# feeds and robots
# --------------------------------------------------------------------------
def build_feed(site: dict, articles: list[dict]) -> None:
    items = ""
    for a in articles[:40]:
        link = site["url"] + a["path"]
        items += f"""
    <item>
      <title>{sx.escape(a['headline'])}</title>
      <link>{sx.escape(link)}</link>
      <guid isPermaLink="true">{sx.escape(link)}</guid>
      <pubDate>{rfc822(a['_date'])}</pubDate>
      <category>{sx.escape(site['cat_by_slug'][a['category']]['name'])}</category>
      <description>{sx.escape(a['summary'])}</description>
    </item>"""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{sx.escape(site['name'])}</title>
    <link>{sx.escape(site['url'])}/</link>
    <atom:link href="{sx.escape(site['url'])}/feed.xml" rel="self" type="application/rss+xml"/>
    <description>{sx.escape(site['description'])}</description>
    <language>{sx.escape(site['locale'])}</language>
    <lastBuildDate>{rfc822(dt.date.today())}</lastBuildDate>{items}
  </channel>
</rss>
"""
    write(DIST / "feed.xml", xml)


def build_sitemap(site: dict, articles: list[dict], by_day: dict) -> None:
    urls = ["/", "/archive/", "/about/", "/contact/", "/privacy/", "/cookies/",
            "/terms/", "/editorial-standards/", "/corrections/"]
    urls += [f"/{c['slug']}/" for c in site["categories"]]
    urls += [f"/{d.year}/{d.month:02d}/{d.day:02d}/" for d in by_day]
    entries = ""
    for u in urls:
        entries += (f"  <url><loc>{sx.escape(site['url'] + u)}</loc>"
                    f"<changefreq>daily</changefreq></url>\n")
    for a in articles:
        entries += (f"  <url><loc>{sx.escape(site['url'] + a['path'])}</loc>"
                    f"<lastmod>{a['_date'].isoformat()}</lastmod>"
                    f"<changefreq>monthly</changefreq></url>\n")
    write(DIST / "sitemap.xml",
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + entries + "</urlset>\n")


def build_misc(site: dict) -> None:
    write(DIST / "robots.txt",
          "User-agent: *\nAllow: /\n\n"
          f"Sitemap: {site['url']}/sitemap.xml\n")
    ad = site["adsense"]
    if ad.get("enabled") and ad.get("publisher_id", "").startswith("ca-pub-"):
        pid = ad["publisher_id"].replace("ca-pub-", "")
        write(DIST / "ads.txt", f"google.com, pub-{pid}, DIRECT, f08c47fec0942fa0\n")
    else:
        write(DIST / "ads.txt",
              "# Replace this file once your AdSense publisher ID is issued.\n"
              "# google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n")
    write(DIST / ".nojekyll", "")

    doc = head(site, title="Page not found", desc="That page does not exist.",
               canonical="/404.html")
    doc += masthead(site)
    doc += ('<div class="wrap"><div class="page"><h1>That page isn\'t here</h1>'
            '<p>The link may be broken, or the page may have moved. Try '
            '<a href="/">today\'s edition</a> or the <a href="/archive/">archive</a>.</p>'
            "</div></div>")
    doc += footer(site)
    write(DIST / "404.html", doc)

    write(DIST / "static" / "favicon.svg", """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="8" fill="#16181d"/>
<path d="M14 46V18h13c6 0 10 3.4 10 8.6 0 3.9-2.2 6.7-5.8 7.9L40 46h-7.6l-7.3-10.6H21V46h-7z" fill="#fbfaf7"/>
<rect x="14" y="50" width="36" height="3" fill="#8f2d2d"/>
</svg>
""")

    write(DIST / "static" / "consent.js", """/* Cookie-consent gate.

   Nothing that sets a non-essential cookie may run before consent is given. The
   AdSense loader is therefore injected from HERE, not from the page head, and only
   after the visitor accepts.

   IMPORTANT: this banner is sufficient only where a certified CMP is not required.
   To serve personalised ads to visitors in the EEA, the UK or Switzerland, Google
   requires a Google-certified CMP integrated with the IAB TCF -- in practice, its
   own Funding Choices / "Privacy & messaging" tool. Set adsense.consent_mode to
   "google_cmp" in site.json once that is configured; this script then stands aside
   and lets Google's CMP do the gating. See the README. */
(function () {
  var KEY = 'rin-consent';
  var bar = document.getElementById('consent');
  var cfg = window.__RIN_ADS || null;

  function read() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }

  function pushSlots() {
    window.adsbygoogle = window.adsbygoogle || [];
    var slots = document.querySelectorAll('ins.adsbygoogle:not([data-rin-filled])');
    for (var i = 0; i < slots.length; i++) {
      slots[i].setAttribute('data-rin-filled', '1');
      try { window.adsbygoogle.push({}); } catch (e) {}
    }
  }

  function loadAds() {
    if (!cfg || !cfg.client) return;
    if (document.getElementById('rin-adsense')) { pushSlots(); return; }
    var s = document.createElement('script');
    s.id = 'rin-adsense';
    s.async = true;
    s.crossOrigin = 'anonymous';
    s.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
            + encodeURIComponent(cfg.client);
    s.onload = pushSlots;
    document.head.appendChild(s);
  }

  // Google's certified CMP owns consent in this mode; the tag is already in the head.
  if (cfg && cfg.mode === 'google_cmp') { pushSlots(); if (bar) bar.hidden = true; return; }

  var stored = read();
  if (stored === 'accept') { loadAds(); }
  else if (!stored && bar) { bar.hidden = false; }

  if (bar) {
    bar.addEventListener('click', function (e) {
      var b = e.target.closest('[data-consent]');
      if (!b) return;
      var v = b.getAttribute('data-consent');
      try { localStorage.setItem(KEY, v); } catch (err) {}
      bar.hidden = true;
      if (v === 'accept') loadAds();
    });
  }

  window.__rinConsent = read;
})();
""")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="serve dist/ after building")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    site = load_site()
    global _BASE_PATH
    _BASE_PATH = site["base_path"]
    articles = load_articles(site)
    if not articles:
        sys.stderr.write("No articles found in content/. Nothing to build.\n")
        return 1
    link_follow_ups(articles)
    by_day = editions_of(articles)

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    shutil.copytree(STATIC, DIST / "static", dirs_exist_ok=True)

    for day, arts in by_day.items():
        for a in arts:
            build_article(site, a, arts)
    build_home(site, by_day, articles)
    build_edition_pages(site, by_day)
    build_categories(site, articles)
    build_archive(site, by_day)
    build_static_pages(site)
    build_feed(site, articles)
    build_sitemap(site, articles, by_day)
    build_misc(site)

    files = sum(1 for _ in DIST.rglob("*") if _.is_file())
    print(f"Built {len(articles)} articles across {len(by_day)} edition(s) "
          f"-> {files} files in dist/")
    print(f"Public URL: {site['url']}/"
          + (f"   (base path {_BASE_PATH}/ applied to internal links)"
             if _BASE_PATH else ""))

    if args.serve:
        import http.server, socketserver, functools
        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(DIST))
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print(f"Serving http://localhost:{args.port}")
            httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
