#!/usr/bin/env python3
"""
Email the review digest. Plain smtplib — no third-party action in the supply chain
of a workflow that holds your publishing credentials.

Environment:
    SMTP_HOST   e.g. smtp.gmail.com
    SMTP_PORT   587 (STARTTLS) or 465 (implicit TLS). Default 587.
    SMTP_USER   the sending account
    SMTP_PASS   an app password, never your account password
    DIGEST_TO   recipient(s), comma-separated. Defaults to SMTP_USER.
    DIGEST_FROM optional display address. Defaults to SMTP_USER.

    python3 scripts/send_digest.py content/2026-08-26 --pr-url https://github.com/...

Exits 0 and prints a notice when SMTP is unconfigured, so a missing secret never
takes the pipeline down — the pull request is the authoritative review surface and
the email is a convenience on top of it.
"""
from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import subprocess
import sys
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the status glyphs
# below. Force UTF-8 on our own output rather than downgrading the output.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent


def build(day_dir: str, fmt: str, pr_url: str) -> str:
    cmd = [sys.executable, str(ROOT / "scripts" / "review_digest.py"), day_dir,
           "--format", fmt]
    if pr_url:
        cmd += ["--pr-url", pr_url]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day_dir")
    ap.add_argument("--pr-url", default="")
    ap.add_argument("--subject", default="")
    args = ap.parse_args()

    host = os.environ.get("SMTP_HOST", "").strip()
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    if not (host and user and password):
        print("SMTP not configured (SMTP_HOST / SMTP_USER / SMTP_PASS). "
              "Skipping the email digest — review in the pull request instead.")
        return 0

    port = int(os.environ.get("SMTP_PORT", "587"))
    to = [x.strip() for x in os.environ.get("DIGEST_TO", user).split(",") if x.strip()]
    sender = os.environ.get("DIGEST_FROM", user)
    day = Path(args.day_dir).name

    try:
        html_body = build(args.day_dir, "html", args.pr_url)
        text_body = build(args.day_dir, "terminal", "")
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"could not build digest: {e.stderr}\n")
        return 1

    msg = EmailMessage()
    msg["Subject"] = args.subject or f"Review: edition of {day}"
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=45) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as s:
                s.starttls(context=ctx)
                s.login(user, password)
                s.send_message(msg)
    except Exception as e:  # noqa: BLE001
        # Never fail the pipeline over a mail problem.
        sys.stderr.write(f"digest email failed ({type(e).__name__}: {e}). "
                         f"The pull request is unaffected.\n")
        return 0

    print(f"Digest emailed to {', '.join(to)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
