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

```bash
python3 gspam.py                 # print what changed since the last run
python3 gspam.py --json          # machine-readable diff
python3 gspam.py --watch 900     # poll every 15 min
python3 gspam.py --publish .     # commit + push the markdown (cron uses this)
python3 test_gspam.py            # self-check
```

State lives in one JSON snapshot (`~/.local/state/gspam.json`). Commits happen only when something actually changed.

Scheduled run:

```cron
*/30 * * * * /home/orges/gspam/gspam.py --publish /home/orges/gspam
```

## Limitations

- Only spam updates are tracked; core and minor updates on the dashboard are ignored.
- Git history is the audit trail — there is no per-update file and no notification system.
- The policy diff is paragraph-level: Google's paragraph rewording appears as a `-`/`+` pair.