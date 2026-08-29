# The standing order for one publishing cycle

This is what the scheduled cloud routine does every two hours. It is kept in the
repository, not in the routine, so it can be changed here without rebuilding the
schedule.

Repository: `mudlbum/richmond-wire`
Live site: https://mudlbum.github.io/richmond-wire/

---

## Where the cycle runs

This runs as a **cloud routine**. The repository is already cloned into the
session's working directory, and the routine has push access because
`mudlbum/richmond-wire` is in its repository list. Use the normal shell, from the
checkout. There is no Windows machine in a cloud run, and no Desktop Commander —
do not go looking for one.

If you are instead running on the editor's own machine with Desktop Commander
available, work in `C:\Users\mudlb\GitHub\richmond-wire` through the Windows
`cmd` shell, which has GitHub Desktop's credentials. Do not use the Linux
`device_bash` shell for the push: it can read and write the same files, but the
push fails there with `could not read Username for 'https://github.com'`.

Either way the steps below are identical apart from the path.

---

## The cycle

### 1. Get the brief

```
git pull --rebase --autostash && python pipeline/brief.py
```

That prints the whole brief: the editorial rules, the beat for this hour, and
every headline published in the last fourteen days. **Read it.** The rules in it
are binding — they are the reason this site can claim to be honest.

### 2. Research

Use web search and actually fetch the pages. For each story:

- **Two independent outlets minimum**, three preferred. Two outlets carrying the
  same wire copy count as one.
- **Every URL in `sources` must be one you actually retrieved.** Never write a
  URL you have not opened. If a page will not load, do not cite it.
- **Never write a number or a quotation you did not read in a source.** Name the
  authority that issued each figure. Mark provisional figures as provisional.
- Where sources disagree, report the disagreement rather than picking the
  more dramatic version.
- Nothing from the archive again, unless it is a genuine development — and then
  it needs a `follow_up` block naming the earlier slug and saying what changed.

### 3. Write the drafts

Write a JSON array to `drafts.json` in the repository root (it is git-ignored),
in exactly the schema the brief sets out. Two stories is the target. **One good
story beats two where the second is filler, and filing nothing is a perfectly
acceptable cycle** — a thin hour is not a failure to work around.

Fill `uncertain` honestly. It is not decorative; the automated gate rejects a
casualty or disaster story that has no entries in it.

### 4. Publish

```
python scripts/publish_cycle.py drafts.json
```

That files the stories into today's edition, drops anything the archive already
covered, runs the editorial gate, proves the site still builds, commits and
pushes. GitHub Actions then attaches a Pexels photograph to each new story,
rebuilds and deploys — live in about two minutes.

### 5. Check it landed

Read the output. It names every story filed and every one dropped, and why.
Then confirm the deploy at https://github.com/mudlbum/richmond-wire/actions.

---

## When something goes wrong

| What happened | What to do |
| --- | --- |
| Every story was dropped as a duplicate | Nothing to publish. Say so and stop. Do not lower the bar to fill the slot. |
| A story failed the editorial gate | It is quarantined in `content/<day>/_rejected/`. The rest published. Do not edit the gate to get a story through. |
| `git pull` hit a conflict | Actions commits photographs to `content/`. Take the remote version of any `content/**` file and re-run the cycle. |
| `git push` was rejected as stale | Re-run step 4; the pull at the start of it will fix a stale clone. |
| `git push` was refused by the git proxy | The routine's repository list does not include this repo. Nothing you can do from inside the run — report it and stop. |
| `git push` to `main` was refused because the branch carries someone else's commits | `main` carries `newswire-bot` commits from the photograph step. Report it and stop; the fix is a repository change, not something to work around. |
| A cycle was missed | Just run the next one. A missing hour needs no catching up. |

## What never happens

- **Never stamp an edition `approved`.** Only a person who has read the stories
  does that, with `scripts/stamp_review.py`. Every story you file says no human
  read it, and that has to stay true.
- **Never weaken the source rules, the gate, or the duplicate thresholds** to
  publish more. The volume is not the point; the honesty is what makes the
  volume defensible.
- **Never invent a URL, a figure, or a quotation.** If it is not in a source you
  opened, it does not go in the article.
