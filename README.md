# google-spam-updates

Auto-generated tracker of Google Search spam updates and spam policy changes.

## What it does

Pulls two public sources on a schedule and commits the result:

| Source | What it tracks |
| --- | --- |
| [Status Dashboard](https://status.search.google.com/products/rGEU1u87FJnkP6W2GwMi/history)<br>incidents feed | Spam update announcements + rollout start/finish |
| [Spam policies](https://developers.google.com/search/docs/essentials/spam-policies)<br>for Google web search | Any edit to the policy doc, as a paragraph-level diff |
| [Search Central blog](https://developers.google.com/search/blog) | Posts announcing new spam policies, full text included |

- [`changelog/`](changelog) — one file per day, `YYYY-MM-DD.md`, indexed in
  [`changelog/README.md`](changelog/README.md). Each entry has a fenced block per change:
  announcement text as `+` lines, policy edits as a unified `diff`, blog post bodies as `+`
  lines. Policy history back to Oct 2022 is rebuilt from archive.org snapshots.
- [`spam-policies.md`](spam-policies.md) — current snapshot of the policy doc.

## Limitations

- Only spam updates are tracked; core and minor dashboard updates are ignored.
- 13 older blog posts (mostly pre-2012, some non-English) publish no machine-readable date, so
  their files fall back to the first of the month.
- Policy diffs are paragraph-level, so a reworded paragraph shows as a `-`/`+` pair rather
  than a word-level diff.
- Fenced lines are hard-wrapped at 100 columns; wrapped tails of a `+`/`-` line are indented
  three spaces so they read as context.
- No notifications — watch the repo, the Actions tab, or `gh run list`.

## Running it

`.github/workflows/update.yml` runs `gspam.py --state state.json --publish .` every 6 hours
and commits when something changed. Trigger it by hand from the Actions tab, or locally:

```bash
python3 gspam.py                 # print what changed since the last run
python3 gspam.py --json          # machine-readable diff
python3 gspam.py --watch 900     # poll every 15 min
python3 gspam.py --publish .     # commit + push the markdown
python3 gspam.py --backfill 49   # rebuild changelog/ from N months of archive.org snapshots
python3 test_gspam.py            # self-check
```

`state.json` (committed) is the snapshot; local runs default to `~/.local/state/gspam.json`
instead. Commits happen only when something actually changed.