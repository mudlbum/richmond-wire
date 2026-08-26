#!/usr/bin/env python3
"""
Turn a day's edition into something a person can actually read and judge.

Used three ways: the body of the review pull request, the email digest, and a
local read-through. The whole point of the review gate is that the reviewer sees
the claims and the sources, not just a filename and a green tick.

    python3 scripts/review_digest.py content/2026-08-26                  # markdown
    python3 scripts/review_digest.py content/2026-08-26 --format html    # email
    python3 scripts/review_digest.py content/2026-08-26 --format terminal
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
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
E = html.escape


def load(day_dir: Path) -> tuple[list[dict], list[dict], dict]:
    arts, rejected = [], []
    meta = {}
    for jf in sorted(day_dir.glob("*.json")):
        if jf.name == "edition.json":
            meta = json.loads(jf.read_text(encoding="utf-8"))
            continue
        a = json.loads(jf.read_text(encoding="utf-8"))
        a["_file"] = jf.name
        arts.append(a)
    for sub, label in (("_rejected", "failed verification"),
                       ("_duplicates", "already published")):
        q = day_dir / sub
        if not q.is_dir():
            continue
        for jf in sorted(q.glob("*.json")):
            try:
                a = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                a = {"headline": jf.name, "sources": [], "body": []}
            a["_file"] = jf.name
            a["_why"] = label
            dup = a.get("_duplicate_of") or {}
            if dup:
                a["_why"] = (f"already published {dup.get('prior_day','')} — "
                             f"{dup.get('reason','')}")
            rejected.append(a)
    arts.sort(key=lambda x: x.get("rank", 99))
    return arts, rejected, meta


def plain_body(a: dict) -> list[str]:
    out = []
    for b in a.get("body", []):
        t = b.get("type", "p")
        if t == "p":
            out.append(b["text"])
        elif t == "h2":
            out.append(f"### {b['text']}")
        elif t in ("ul", "ol"):
            out.extend(f"  - {i}" for i in b.get("items", []))
        elif t == "quote":
            out.append(f"> {b['text']}\n> — {b.get('cite', 'unattributed')}")
        elif t == "box":
            out.append(f"[{b.get('title', 'Note')}] "
                       + (b.get("text", "") or "; ".join(str(i) for i in b.get("items", []))))
    return out


def domains(a: dict) -> list[str]:
    ds = []
    for s in a.get("sources", []):
        m = re.match(r"https?://([^/]+)", s.get("url", ""))
        if m:
            d = m.group(1).replace("www.", "")
            if d not in ds:
                ds.append(d)
    return ds


# GitHub rejects a pull request body over 65536 characters. A ten-story edition
# lands around 55k, so a wordy day would silently fail to open the review PR --
# which would mean no review at all. Budget for it instead.
PR_BODY_LIMIT = 60000


def markdown(day: str, arts: list[dict], rejected: list[dict], hold: int,
             limit: int = PR_BODY_LIMIT) -> str:
    """Render the digest, trimming article bodies only if the whole thing would
    exceed `limit`. Headlines, deks, sources and uncertainty flags are never
    trimmed -- they are what the reviewer needs most."""
    full = _markdown(day, arts, rejected, hold, body_paras=None)
    if len(full) <= limit:
        return full
    for cap in (12, 9, 7, 5, 4, 3):
        trimmed = _markdown(day, arts, rejected, hold, body_paras=cap)
        if len(trimmed) <= limit:
            return trimmed
    return _markdown(day, arts, rejected, hold, body_paras=2)


def _markdown(day: str, arts: list[dict], rejected: list[dict], hold: int,
              body_paras: int | None) -> str:
    L = [f"# Edition of {day} — {len(arts)} stories for review", ""]
    L.append(f"**Merging this pull request publishes the edition and records it as "
             f"editor-approved.** If nobody merges it within {hold} hours it publishes "
             f"anyway, labelled on the site as not reviewed by an editor.")
    L.append("")
    L.append("Reject a single story by deleting its file in this PR. Fix wording by editing "
             "the file. Reject the whole edition by closing the PR.")
    L.append("")
    L.append("## Checklist")
    L.append("")
    for item in [
        "Every headline matches what the article actually says",
        "No private individual is named as a victim of crime, accident or disaster",
        "Contested claims are attributed, not asserted in our voice",
        "Casualty and developing figures are marked provisional",
        "Nothing reads as taking a political side",
        "Science and health claims are not stronger than the study supports",
    ]:
        L.append(f"- [ ] {item}")
    L.append("")
    L.append("---")
    L.append("")

    for a in arts:
        cat = a.get("category", "?").upper()
        L.append(f"## {a.get('rank', '?')}. [{cat}] {a.get('headline', '(no headline)')}")
        L.append("")
        L.append(f"*{a.get('dek', '')}*")
        L.append("")
        ds = domains(a)
        L.append(f"`{a['_file']}` · {len(ds)} independent source domain(s): "
                 + ", ".join(f"`{d}`" for d in ds))
        L.append("")
        blocks = plain_body(a)
        if body_paras is not None and len(blocks) > body_paras:
            L.extend(blocks[:body_paras])
            L.append("")
            L.append(f"*… {len(blocks) - body_paras} more paragraph(s). This digest was "
                     f"trimmed to fit GitHub's pull-request body limit — read the full "
                     f"text in `{a['_file']}` under the Files changed tab.*")
        else:
            L.extend(blocks)
        L.append("")
        if a.get("uncertain"):
            L.append("**Flagged as not yet confirmed:**")
            L.extend(f"- {u}" for u in a["uncertain"])
            L.append("")
        L.append("<details><summary>Sources</summary>")
        L.append("")
        for s in a.get("sources", []):
            note = f" — *{s['note']}*" if s.get("note") else ""
            L.append(f"- **{s.get('outlet', '?')}** — "
                     f"[{s.get('title', s.get('url', ''))}]({s.get('url', '')}){note}")
        L.append("")
        L.append("</details>")
        L.append("")
        L.append("---")
        L.append("")

    if rejected:
        L.append(f"## Dropped before you saw them ({len(rejected)})")
        L.append("")
        L.append("Filtered automatically — either they failed source verification or "
                 "they repeated a story we have already published. Listed so the "
                 "filtering is visible to you rather than silent.")
        L.append("")
        for a in rejected:
            L.append(f"- `{a['_file']}` — {a.get('headline', '(unparseable)')}")
            if a.get("_why"):
                L.append(f"  - _{a['_why']}_")
        L.append("")
    return "\n".join(L)


def to_html(day: str, arts: list[dict], rejected: list[dict], hold: int,
            pr_url: str = "") -> str:
    css = ("body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
           "color:#16181d;background:#fbfaf7;margin:0;padding:24px}"
           ".w{max-width:680px;margin:0 auto}"
           "h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:26px 0 4px;"
           "padding-top:18px;border-top:1px solid #e0dcd3}"
           "h3{font-size:14px;margin:16px 0 4px;color:#6b7280;text-transform:uppercase;"
           "letter-spacing:.08em}"
           ".tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.1em;"
           "color:#8f2d2d}"
           ".dek{color:#3d434e;margin:4px 0 8px}"
           ".meta{font-size:12px;color:#6b7280;margin:0 0 10px}"
           ".warn{background:#fdf6e7;border:1px solid #e3c98a;padding:12px 14px;"
           "border-radius:4px;margin:0 0 18px;font-size:14px}"
           ".unc{background:#f5f2eb;border-left:3px solid #8f2d2d;padding:10px 12px;"
           "font-size:14px;margin:10px 0}"
           "ul{padding-left:20px}a{color:#1d4ed8}"
           ".btn{display:inline-block;background:#8f2d2d;color:#fff!important;padding:10px 18px;"
           "border-radius:4px;text-decoration:none;font-weight:600;margin:8px 0}"
           "blockquote{border-left:3px solid #c9c3b6;margin:10px 0;padding-left:12px;"
           "color:#3d434e}")
    P = [f"<style>{css}</style><div class='w'>",
         f"<h1>Edition of {E(day)} — {len(arts)} stories</h1>",
         f"<div class='warn'><strong>Publishes automatically in {hold} hours.</strong> "
         f"Merge the pull request to publish it now and record it as editor-approved. "
         f"If the window expires it publishes anyway and every article will say it was "
         f"not read by an editor.</div>"]
    if pr_url:
        P.append(f"<a class='btn' href='{E(pr_url)}'>Open the review pull request</a>")

    for a in arts:
        P.append(f"<h2><span class='tag'>{E(a.get('category', '?').upper())}</span><br>"
                 f"{E(a.get('headline', ''))}</h2>")
        P.append(f"<p class='dek'>{E(a.get('dek', ''))}</p>")
        ds = domains(a)
        P.append(f"<p class='meta'>{len(ds)} source domain(s): {E(', '.join(ds))}</p>")
        for line in plain_body(a):
            if line.startswith("### "):
                P.append(f"<h3>{E(line[4:])}</h3>")
            elif line.startswith("> "):
                P.append(f"<blockquote>{E(line[2:].replace(chr(10) + '> ', ' — '))}"
                         f"</blockquote>")
            elif line.startswith("  - "):
                P.append(f"<ul><li>{E(line[4:])}</li></ul>")
            else:
                P.append(f"<p>{E(line)}</p>")
        if a.get("uncertain"):
            P.append("<div class='unc'><strong>Not yet confirmed:</strong><ul>"
                     + "".join(f"<li>{E(str(u))}</li>" for u in a["uncertain"])
                     + "</ul></div>")
        P.append("<p class='meta'>Sources: " + " · ".join(
            f"<a href='{E(s.get('url', ''))}'>{E(s.get('outlet', '?'))}</a>"
            for s in a.get("sources", [])) + "</p>")

    if rejected:
        P.append(f"<h2>Dropped before you saw them ({len(rejected)})</h2><ul>"
                 + "".join(f"<li>{E(a.get('headline', a['_file']))}"
                           + (f"<br><small>{E(a.get('_why',''))}</small>"
                              if a.get('_why') else "")
                           + "</li>" for a in rejected)
                 + "</ul>")
    P.append("</div>")
    return "\n".join(P)


def terminal(day: str, arts: list[dict], rejected: list[dict]) -> str:
    L = [f"\n{'=' * 72}", f"  EDITION OF {day} — {len(arts)} STORIES", f"{'=' * 72}"]
    for a in arts:
        L.append(f"\n{'-' * 72}")
        L.append(f"[{a.get('rank', '?')}] {a.get('category', '?').upper()}  ({a['_file']})")
        L.append(f"{'-' * 72}")
        L.append(f"\n{a.get('headline', '')}\n")
        L.append(f"  {a.get('dek', '')}\n")
        for line in plain_body(a):
            L.append(f"  {line}")
        if a.get("uncertain"):
            L.append("\n  NOT YET CONFIRMED:")
            L.extend(f"    · {u}" for u in a["uncertain"])
        L.append("\n  SOURCES:")
        for s in a.get("sources", []):
            L.append(f"    · {s.get('outlet', '?')}: {s.get('url', '')}")
    if rejected:
        L.append(f"\n{'=' * 72}\n  DROPPED BY THE AUTOMATED GATE ({len(rejected)})")
        L.extend(f"    · {a.get('headline', a['_file'])}" for a in rejected)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    ap.add_argument("--format", choices=["markdown", "html", "terminal"],
                    default="markdown")
    ap.add_argument("--pr-url", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    day_dir = Path(args.day_dir)
    if not day_dir.is_dir():
        sys.stderr.write(f"not a directory: {day_dir}\n")
        return 2
    day = day_dir.name

    try:
        hold = json.loads((ROOT / "site.json").read_text())["editorial"]["hold_hours"]
    except Exception:  # noqa: BLE001
        hold = 12

    arts, rejected, _ = load(day_dir)
    if not arts:
        sys.stderr.write(f"no articles in {day_dir}\n")
        return 2

    if args.format == "markdown":
        out = markdown(day, arts, rejected, hold)
        if len(out) > PR_BODY_LIMIT:
            sys.stderr.write(f"warning: digest is {len(out)} chars, over the "
                             f"{PR_BODY_LIMIT} budget\n")
    elif args.format == "html":
        out = to_html(day, arts, rejected, hold, args.pr_url)
    else:
        out = terminal(day, arts, rejected)

    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"wrote {args.out} ({len(out)} chars)")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
