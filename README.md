# Richmond International Newswire

A static news site that publishes ten verified international stories a day,
automatically, with every source named and linked.

No dependencies. Python 3.11+ standard library only.

```
python3 build.py            # build dist/
python3 build.py --serve    # build and serve at http://localhost:8000
```

---

## How a day works

```
 09:30 UTC  propose-edition.yml
     ↓
 generate_edition.py   5 beats × 2 stories, Claude API with web search
     ↓                 each beat is shown the last 14 days of coverage first
 dedupe.py             drops anything already published, and any story two
     ↓                 beats filed twice this morning
 validate.py           automated gate — hard failures are quarantined
     ↓                 (fewer than 3 survivors → no edition proposed at all)
 fetch_images.py       Pexels photo per story, honest attribution
     ↓
 opens a PULL REQUEST  edition stamped "pending" · full digest in the PR body
     ↓                 + the same digest emailed to you
     │
     ├─── you merge it ──────────→ publish.yml stamps "approved" → builds → deploys
     │                             every article says an editor read it
     │
     └─── 12 hours pass ─────────→ release-on-timeout.yml stamps "auto", merges
                                   → publish.yml builds → deploys
                                   every article says NO editor read it,
                                   and the edition carries a warning box
```

### The review gate

An edition never publishes straight from the generator. It lands in a pull request
first, with a readable digest: every headline, the full text, the source domains,
what each article flags as unconfirmed, and a list of what the automated gate threw
out. You can read it in the GitHub app on your phone.

- **Merge** → published and recorded as editor-approved.
- **Edit a file in the PR, then merge** → your version publishes.
- **Delete a file in the PR** → that story is dropped, the rest publishes.
- **Close the PR** → nothing publishes that day.
- **Do nothing for 12 hours** → it publishes, labelled as unreviewed.

### No repeats

Readers come back daily, so filing the same story twice is the fastest way to look
like an unattended machine. Three layers stop it:

1. **Before writing.** Every beat is handed the last 14 days of headlines and told
   not to file them again. `pipeline/coverage.py --days 14` prints exactly what the
   researcher sees.
2. **Within the run.** Beats research independently, so two can reach the same
   story on a busy morning. The second one is dropped as it arrives.
3. **Against the archive.** `pipeline/dedupe.py` compares every candidate to the
   past three weeks on two axes — content-word overlap and whether they cite the
   same specific source articles. Numbers are deliberately ignored: a toll moving
   from 8 to 22 is exactly when two stories ARE the same one.

Dropped articles go to `content/<day>/_duplicates/` and are listed in the review
digest, so you can see what was filtered and disagree with it.

### Follow-ups are allowed; rewrites are not

Developing stories legitimately continue. The distinction the code enforces is
whether the article carries new facts, which it cannot measure — so instead it
requires the article to *declare* the continuation:

```json
"follow_up": {
  "of": "nepal-rasuwa-flash-flood-bhote-koshi",
  "whats_new": "A commission of inquiry has been appointed, with a 45-day deadline."
}
```

That declaration is rendered on the page as a "This continues an earlier story" box
with a link back and the what-changed line, so it costs something to claim falsely.

A declared follow-up whose text is still ~72% the same as the earlier article is
dropped anyway — that is a rewrite wearing a follow-up label. Tested behaviour:

| Case | Text overlap | Outcome |
| --- | --- | --- |
| Same story re-filed, reworded headline | 98% | dropped |
| Same story, *declared* a follow-up | 98% | dropped — rewrite, not development |
| Real development, declared | 66% | **kept**, renders the follow-up box |
| Same development, not declared | 66% | dropped |
| Two beats filing one story in one edition | 87% | second one dropped |
| Genuinely different story | 10% | kept |

Tune the thresholds in `pipeline/coverage.py` (`DUP_TEXT`, `DUP_SOURCE`,
`REBUILD`, `WINDOW_DAYS`) if the wire runs too tight or too loose.

### Why the labelling is not optional

You chose auto-publish so the daily cadence survives your busy days. The cost is that
some articles reach readers unread, so the site cannot make a blanket claim that
everything is reviewed — that claim would be false on exactly the days it matters
most, and a false statement about editorial process is its own problem under Google's
publisher policies quite apart from being dishonest.

So the wording is **per edition**, driven by `content/<day>/edition.json`:

| `review.status` | What the site says |
| --- | --- |
| `approved` | "Read and approved by the editor before publication" |
| `auto` | "Published on the review timer, NOT read by an editor" + a warning box on the edition |
| missing / unreadable | treated as unreviewed — never as approved |

`scripts/release_pending.py` will only ever promote a `pending` edition. The timeout
path stamps `auto` **on the branch, before merging**, so an unreviewed edition
arrives on main already labelled and cannot be upgraded. Do not reorder those steps.

### Turning the timer off

If you would rather miss a day than publish unread:

1. Comment out the `schedule:` block in `.github/workflows/release-on-timeout.yml`.
2. Set `editorial.review_mode` to `"hold_until_approved"` in `site.json`.

Editions then wait indefinitely, and the footer wording changes to "No article is
published until an editor has read and released it." That claim then holds, because
the mechanism makes it hold.

## Layout

```
site.json                      name, URL, categories, AdSense IDs — edit this first
build.py                       static site generator
content/YYYY-MM-DD/*.json      one file per article; the archive lives here
pages/*.md                     about, contact, privacy, cookies, terms, standards
static/                        stylesheet, favicon, consent script
pipeline/
  editorial_prompt.md          the rules the writing model is held to
  generate_edition.py          research + write, one call per beat
  coverage.py                  what we have published; similarity scoring
  dedupe.py                    the no-repeats gate
  validate.py                  the editorial gate
  fetch_images.py              Pexels fetch with attribution rules
scripts/
  review_digest.py             the PR body / email digest a human actually reads
  send_digest.py               emails it (plain smtplib, no third-party action)
  stamp_review.py              writes review status into edition.json
  release_pending.py           promotes pending → approved on merge
  check_links.py               post-build link and metadata check
.github/workflows/
  propose-edition.yml          09:30 UTC — research, verify, open the review PR
  release-on-timeout.yml       hourly — merge anything past the 12-hour window
  publish.yml                  on merge to main — record, build, deploy
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
| Secret | `ANTHROPIC_API_KEY` | console.anthropic.com |
| Secret | `PEXELS_API_KEY` | pexels.com/api — free |
| Secret *(optional)* | `SMTP_HOST` | `smtp.gmail.com` for Gmail |
| Secret *(optional)* | `SMTP_PORT` | `587` |
| Secret *(optional)* | `SMTP_USER` | the sending address |
| Secret *(optional)* | `SMTP_PASS` | a Gmail **app password**, not your account password |
| Secret *(optional)* | `DIGEST_TO` | where the review digest goes |
| Variable | `ANTHROPIC_MODEL` | optional; check the current model list and set it |

**The five SMTP secrets are optional.** Without them the pipeline runs exactly the
same and simply skips the email. You still find out an edition is waiting, because
the review pull request is assigned to you — GitHub sends its own notification for
that — and the Actions run summary carries the link and the counts.

Add them later if you want the full digest in your inbox. Gmail app passwords need
2-Step Verification switched on, then are created at myaccount.google.com →
Security → App passwords.

**Actions also needs permission to open pull requests:** Settings → Actions → General
→ Workflow permissions → tick "Allow GitHub Actions to create and approve pull
requests".

Without `PEXELS_API_KEY` the site still builds — every article falls back to a
generated section graphic.

### 4. Set your real URL

Edit `site.json` and change `url` to your actual Pages address (or your custom
domain). Canonical tags, the sitemap and the RSS feed all read from it, so a wrong
value here quietly breaks SEO.

While you are in there, change the three email addresses under `publisher`. They
appear on the contact, privacy and corrections pages, and Google checks that a
contact route exists.

### 5. Run one edition by hand first

Actions → **Daily edition** → Run workflow. Watch it before trusting the cron.

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
python3 pipeline/coverage.py --stats                          # what is published
python3 pipeline/dedupe.py content/2026-08-26 --dry-run      # repeats check
python3 pipeline/validate.py content/2026-08-26               # automated gate
python3 pipeline/validate.py content/2026-08-26 --check-links # + fetch every source
python3 scripts/review_digest.py content/2026-08-26 --format terminal   # read it
python3 build.py && python3 scripts/check_links.py            # build + link audit
```

To see how an edition looks in each review state before you trust the pipeline:

```bash
python3 scripts/stamp_review.py content/2026-08-26 --status approved --by you
python3 build.py --serve      # then flip to --status auto and rebuild
```
