# Richmond International Newswire

A static news site that files verified international stories **every two hours**,
around the clock, with every source named and linked.

No dependencies. Python 3.11+ standard library only. No API key: the research and
writing are done by a Claude session on your own machine, on a schedule, using
your subscription rather than metered API credits.

```
python build.py            # build dist/
python build.py --serve    # build and serve at http://localhost:8000
```

---

## How a cycle works

Twelve times a day, on the even hour (UTC), a scheduled Claude task wakes up on
your computer and runs one cycle:

```
 every 2 hours, on your machine
     ↓
 pipeline/brief.py        assembles the brief: the editorial rules, the beat for
     ↓                    this hour, and the last 14 days of headlines to avoid
 the desk researches      web search + reading real sources, two stories,
     ↓                    written to a drafts JSON file
 scripts/publish_cycle.py ────────────────────────────────────────────────┐
     ↓                                                                    │
     ├─ file_stories.py   structural gate, timestamps, numbers them into  │
     │                    content/<today>/ next to earlier cycles         │
     ├─ dedupe.py         drops anything the archive already covered      │
     ├─ validate.py       editorial gate; hard failures are quarantined   │
     ├─ build.py          proves the site still builds before pushing     │
     └─ git commit && push ───────────────────────────────────────────────┘
     ↓
 GitHub Actions · publish.yml
     ↓
 fetch_images.py          a Pexels photograph per new story, honest attribution
     ↓                    (this is where the Pexels key lives, not on your machine)
 build + deploy           the new stories are live within a couple of minutes
```

A cycle that finds nothing worth filing pushes nothing. That is a correct
outcome, not a failure — see *No repeats* below.

### The beat rotation

Two stories a cycle would cover one topic to death if every cycle chased the same
news. So each slot is handed a different beat, and a full day covers all of them:

| UTC | Beat | UTC | Beat |
| --- | --- | --- | --- |
| 00:00 | World | 12:00 | Technology & science |
| 02:00 | Technology & science | 14:00 | Environment & society |
| 04:00 | Environment & society | 16:00 | Economy |
| 06:00 | Economy | 18:00 | Sport & culture |
| 08:00 | Sport & culture | 20:00 | World |
| 10:00 | World | 22:00 | Technology & science |

Over a day that is three world cycles, three technology and science, and two
each of the rest — the wire leads on world news, which is what it is for. The
beat is picked from the hour rather than a fixed timetable, so the rotation still
covers everything if the schedule drifts onto odd hours or you change the
interval.

The rotation lives in `ROTATION` at the top of `pipeline/brief.py`. Change it
there and the schedule follows.

### Nothing claims a review that did not happen

There is no review gate any more: stories go live as they are filed. So every
article says exactly that — *"No human read this article before it went live"* —
and each day carries an **Unreviewed edition** box until you say otherwise.

When you have read a day, stamp it:

```powershell
cd $env:USERPROFILE\GitHub\richmond-wire
git pull
python scripts\stamp_review.py content\2026-08-29 --status approved --by "Dave"
git add content && git commit -m "Record editorial approval" && git push
```

Do that at the **end** of a day, not during it: filing new stories resets the day
to unreviewed, because the banner speaks for every story on the page and stories
filed at 22:00 were not in the ones you read at noon.

Only ever stamp a day you actually read. That line is a claim to your readers, and
the whole design exists to keep it true.

### If you would rather review first

Set `editorial.review_mode` back to `"hold_until_approved"` in `site.json` and
stop the scheduled task from pushing (`--no-push` in the cycle command). Stories
then accumulate locally and go live only when you push them yourself.

## Layout

```
site.json                      name, URL, categories, AdSense IDs — edit this first
build.py                       static site generator
content/YYYY-MM-DD/*.json      one file per article; the archive lives here
pages/*.md                     about, contact, privacy, cookies, terms, standards
static/                        stylesheet, favicon, consent script
pipeline/
  editorial_prompt.md          the rules the writing desk is held to
  brief.py                     assembles one cycle's brief: rules + beat + archive
  file_stories.py              turns a cycle's drafts into filed articles
  coverage.py                  what we have published; similarity scoring
  dedupe.py                    the no-repeats gate
  validate.py                  the editorial gate
  fetch_images.py              Pexels fetch with attribution rules
scripts/
  publish_cycle.py             one cycle end to end: file → dedupe → gate → push
  stamp_review.py              writes review status into edition.json
  review_digest.py             a readable digest of a day, for reviewing offline
  send_digest.py               emails it (plain smtplib, no third-party action)
  release_pending.py           promotes pending → approved on a review-PR merge
  check_links.py               post-build link and metadata check
.github/workflows/
  publish.yml                  on push to main — photographs, build, deploy
  backfill-images.yml          manual — attach photographs to an older edition
  build-check.yml              on push/PR — validate and build
dist/                          build output (git-ignored)
```

---

## Setup

### 1. Put it on GitHub

```bash
cd richmond-wire
git init && git add . && git commit -m "Initial commit"
gh repo create richmond-wire --public --source=. --push
```

### 2. Turn on Pages

Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.

### 3. Add the secrets

**Settings → Secrets and variables → Actions**

| Kind | Name | Where to get it |
| --- | --- | --- |
| Secret | `PEXELS_API_KEY` | pexels.com/api — free |
| Secret *(optional)* | `SMTP_HOST` | `smtp.gmail.com` for Gmail |
| Secret *(optional)* | `SMTP_PORT` | `587` |
| Secret *(optional)* | `SMTP_USER` | the sending address |
| Secret *(optional)* | `SMTP_PASS` | a Gmail **app password**, not your account password |
| Secret *(optional)* | `DIGEST_TO` | where a daily digest would go |

**There is no `ANTHROPIC_API_KEY` any more.** The writing happens in a Claude
session on your own machine, so nothing here calls a paid API. `PEXELS_API_KEY` is
the only secret the pipeline needs, and it is used by Actions rather than by your
computer.

**The SMTP secrets are optional** and unused by the two-hourly wire. They exist for
`scripts/send_digest.py` if you ever want a day mailed to you.

Without `PEXELS_API_KEY` the site still builds — every article falls back to a
generated section graphic.

### 4. Set your real URL

Edit `site.json` and change `url` to your actual Pages address (or your custom
domain). Canonical tags, the sitemap and the RSS feed all read from it, so a wrong
value here quietly breaks SEO.

While you are in there, change the three email addresses under `publisher`. They
appear on the contact, privacy and corrections pages, and Google checks that a
contact route exists.

### 5. Run one cycle by hand first

Ask Claude on your desktop to run a cycle now, or run the mechanical half yourself
against a drafts file you have written:

```powershell
python scripts\publish_cycle.py drafts.json --no-push   # everything but the push
```

Watch one complete cycle before trusting the schedule.

---

## AdSense

The site is built to satisfy Google's publisher requirements, but it is switched off. Read
this whole section before turning it on — there are two things that will get you rejected
and neither is obvious.

### Before applying

Publish roughly 20–30 editions of real content first. A site with one day on it reads as
thin to a reviewer no matter how good the day is. Point a custom domain at it if you have
one; a bare `github.io` subdomain is accepted less often.

Then replace every placeholder. `build.py` refuses to build with `adsense.enabled: true`
while any of these are still fake, so you cannot ship them by accident:

- `site.url` — canonical tags, sitemap and RSS all read from it
- `adsense.publisher_id`
- the three addresses under `publisher` — Google checks the contact route works

### The consent problem (read this)

**Google requires a Google-certified consent management platform, integrated with the IAB
Transparency and Consent Framework, to serve personalised ads to visitors in the EEA, the UK
or Switzerland.** A hand-rolled banner does not satisfy this, however well built.

The banner in this repo does gate ad loading correctly — the AdSense script is injected by
`consent.js` after acceptance, not from the page head, so rejecting means the request is
never made. That is enough where a certified CMP is not required. It is **not** enough for
Europe.

So: set up Google's **Funding Choices** ("Privacy & messaging" in the AdSense UI), then set
`adsense.consent_mode` to `"google_cmp"` in `site.json`. The built-in banner then stands
aside and Google's CMP handles consent. Leave it as `"self"` only if you are certain you are
not serving European visitors, which for an international news site you almost certainly are.

### The AI content problem (read this too)

Google's spam policies define **scaled content abuse** as "many pages generated for
the primary purpose of manipulating search rankings and not helping users… using
generative AI tools… to generate many pages without adding value for users." The test
is whether the output adds value, not whether AI was involved — but ten AI-generated
pages a day, indefinitely, is exactly the *shape* the policy targets.

What puts this site on the defensible side: a two-independent-source minimum, every
source URL actually fetched before publishing, original prose rather than reformatted
excerpts, a mandatory "what is not yet confirmed" section, a human review gate, and a
public AI disclosure that is accurate per article.

That last point is load-bearing. **Do not soften the unreviewed label to make the
site look better.** Claiming editorial review that did not happen is a
misrepresentation problem in its own right, and it would also throw away the thing
that makes the reviewed articles worth something. If the unreviewed badge starts
appearing more often than you like, the fix is to review more often or to switch to
`hold_until_approved` — not to change the wording.

### Copyright at volume

Summarising facts with attribution and brief quotation is standard news practice and is not
in itself infringement. The site is structurally careful about it: original wording, never
copied sentences, every source linked and credited.

The real exposure is cumulative rather than per-article. Systematically paraphrasing the same
premium wire and finance outlets, ten stories a day, indefinitely, while monetising the
result, is the pattern currently being litigated across the industry. Attribution reduces
that risk; it does not eliminate it. Worth a lawyer's read specifically on volume before you
scale past a pilot — separately from the privacy-policy review below.

### What is already in place

| Requirement | Where |
| --- | --- |
| Original content, not scraped | Every article is an original summary; sources linked, never copied |
| Clear navigation | Section nav in the masthead, footer link block on every page |
| Privacy policy with the Google-required disclosures | `pages/privacy.md` — third-party vendor cookies, opt-out links, GDPR bases |
| Cookie policy and working consent gate | `pages/cookies.md`; ad script loads only after acceptance |
| About page with ownership and funding | `pages/about.md` |
| Contact route | `pages/contact.md`, three addresses |
| Editorial standards and per-article AI disclosure | `pages/editorial-standards.md` |
| Human review before publication | PR gate; 12-hour window; status recorded per edition |
| Corrections policy | `pages/corrections.md` |
| `ads.txt` | Generated by `build.py` from `site.json` |
| `robots.txt`, `sitemap.xml`, RSS | Generated by `build.py` |
| Structured data | `NewsArticle` + `BreadcrumbList` JSON-LD per article |
| Mobile-responsive, accessible | Skip link, alt text on every image, light/dark, print styles |
| Ad placement not deceptive | Every slot labelled "Advertisement" and separated from body text |

### Still yours to do

- Real emails, real domain, real publisher ID.
- Funding Choices CMP configured, `consent_mode` switched to `google_cmp`.
- A lawyer's review of the privacy policy, terms, and the volume question above. These are
  careful drafts, not legal advice.

## The image rule

Article photographs come from Pexels and are captioned:

> Illustrative stock photograph. It was not taken at the event described in this
> article and does not depict any person involved in it. Photo: [name] via Pexels.

This is deliberate. Pexels images are real photographs of real people. Placing one
beside a news event without saying so implies those people were there. The caption
is not a formality — it is the thing that keeps the picture honest.

`fetch_images.py` goes further: any article whose text matches the sensitive-topic
list (deaths, attacks, disasters, kidnapping, refugees, and so on) never gets a
photograph of people at all. It gets a neutral object or landscape, or a generated
section graphic.

The site uses no AI-generated images. If you decide to add them, label them as
AI-generated in the caption and update `pages/editorial-standards.md` first.

---

## Editing an edition after publication

Change the JSON in `content/YYYY-MM-DD/`, rebuild, push. Then add a dated note to
the article's body recording what changed — that is what
`pages/corrections.md` promises readers, and the promise is the point.

## Local checks

```bash
python pipeline/coverage.py --stats                           # what is published
python pipeline/brief.py                                     # this hour's brief
python pipeline/file_stories.py drafts.json --dry-run        # what would be filed
python pipeline/dedupe.py content/2026-08-29 --dry-run       # repeats check
python pipeline/validate.py content/2026-08-29                # automated gate
python pipeline/validate.py content/2026-08-29 --check-links  # + fetch every source
python scripts/review_digest.py content/2026-08-29 --format terminal   # read a day
python build.py && python scripts/check_links.py              # build + link audit
```

To see how an edition looks in each review state before you trust the pipeline:

```bash
python3 scripts/stamp_review.py content/2026-08-26 --status approved --by you
python3 build.py --serve      # then flip to --status auto and rebuild
```
