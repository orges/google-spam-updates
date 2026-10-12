# google-spam-updates

Auto-generated tracker of Google Search spam updates and spam policy changes.

## What it does

Pulls two public sources on a schedule and commits the result:

| Source | What it tracks |
| --- | --- |
| [Google Search Status Dashboard](https://status.search.google.com/products/rGEU1u87FJnkP6W2GwMi/history) incidents feed | Spam update announcements + rollout start/finish |
| [Spam policies for Google web search](https://developers.google.com/search/docs/essentials/spam-policies) | Any edit to Google's spam policy document |

- [`CHANGELOG.md`](CHANGELOG.md) — every spam update, newest first, with rollout status and a dashboard link.
- [`spam-policies.md`](spam-policies.md) — current snapshot of the policy doc. **Git history of this file is the policy change history**, so `git log -p spam-policies.md` shows exactly what Google edited and when.

## Running it

`.github/workflows/update.yml` runs `gspam.py --state state.json --publish .` every 6 hours and commits when something changed. Trigger it by hand from the Actions tab, or locally:

```bash
python3 gspam.py                 # print what changed since the last run
python3 gspam.py --json          # machine-readable diff
python3 gspam.py --watch 900     # poll every 15 min
python3 gspam.py --publish .     # commit + push the markdown
python3 test_gspam.py            # self-check
```

`state.json` (committed) is the snapshot; local runs default to `~/.local/state/gspam.json` instead. Commits happen only when something actually changed.

## Limitations

- Only spam updates are tracked; core and minor updates on the dashboard are ignored.
- Git history is the audit trail — there is no per-update file and no notification system.
- The policy diff is paragraph-level: Google's paragraph rewording appears as a `-`/`+` pair.