# google-spam-updates

Auto-generated tracker of Google Search spam updates and spam policy changes.

## What it does

Pulls two public sources on a schedule and commits the result:

| Source | What it tracks |
| --- | --- |
| [Google Search Status Dashboard](https://status.search.google.com/products/rGEU1u87FJnkP6W2GwMi/history) incidents feed | Spam update announcements + rollout start/finish |
| [Spam policies for Google web search](https://developers.google.com/search/docs/essentials/spam-policies) | Any edit to Google's spam policy document, as a paragraph-level diff |

- [`changelog/`](changelog) — one file per day, `YYYY-MM-DD.md`, indexed in [`changelog/README.md`](changelog/README.md). Each entry has a fenced block per change: announcement text as `+` lines, policy edits as a unified `diff`. History back to Oct 2022 is rebuilt from [archive.org](https://web.archive.org/web/*/https://developers.google.com/search/docs/essentials/spam-policies).
- [`spam-policies.md`](spam-policies.md) — current snapshot of the policy doc.

## Limitations

- Only spam updates are tracked; core and minor dashboard updates are ignored.
- Policy diffs are paragraph-level, so a reworded paragraph shows as a `-`/`+` pair rather than a word-level diff.
- No notifications — watch the repo, the Actions tab, or `gh run list`.

## Running it

`.github/workflows/update.yml` runs `gspam.py --state state.json --publish .` every 6 hours and commits when something changed. Trigger it by hand from the Actions tab, or locally:

```bash
python3 gspam.py                 # print what changed since the last run
python3 gspam.py --json          # machine-readable diff
python3 gspam.py --watch 900     # poll every 15 min
python3 gspam.py --publish .     # commit + push the markdown
python3 gspam.py --backfill 49   # rebuild changelog/ from N months of archive.org snapshots
python3 test_gspam.py            # self-check
```

`state.json` (committed) is the snapshot; local runs default to `~/.local/state/gspam.json` instead. Commits happen only when something actually changed.