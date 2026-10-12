#!/usr/bin/env python3
"""gspam - report changes to Google's spam updates and spam policies.

Sources:
  - Google Search Status Dashboard incidents feed, filtered to spam updates.
  - The spam policies doc, stored as a text snapshot and unified-diffed.
State: one JSON snapshot (default ~/.local/state/gspam.json).

  gspam.py                 # print what changed since last run
  gspam.py --watch 900     # poll every 15 min
  gspam.py --json          # machine-readable diff
  gspam.py --publish .     # also commit+push CHANGELOG.md / spam-policies.md here
  gspam.py --state /tmp/x.json --watch 60 --json
"""
import argparse
import difflib
import gzip
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib

FEED = "https://status.search.google.com/incidents.json"
BASE = "https://status.search.google.com/incidents/"
DOC = "https://developers.google.com/search/docs/essentials/spam-policies"
DEFAULT_STATE = os.environ.get(
    "GSPAM_STATE", os.path.expanduser("~/.local/state/gspam.json")
)
DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))
SPAM = re.compile(r"spam", re.I)
JUNK = re.compile(r"^(Home|Search Central|Documentation|Send feedback|Previous|Next"
                  r"|Was this helpful\??|Last updated .*)$", re.I)
BLOCK = re.compile(r"</?(p|li|h[1-6]|tr|dt|dd|blockquote|pre|ul|ol|table|div)\b[^>]*>", re.I)
ENTITIES = {"&amp;": "&", "&nbsp;": " ", "&#39;": "'", "&quot;": '"',
            "&lt;": "<", "&gt;": ">", "&rsquo;": "'", "&hellip;": "..."}


def get(url):
    req = urllib.request.Request(url, headers={"user-agent": "gspam/1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


def doc_text(url=DOC):
    """Return the spam policies doc as one paragraph per line, boilerplate stripped."""
    html = get(url)
    art = re.search(r"<article.*?</article>", html, re.S)
    t = re.sub(r"<(script|style|svg)\b.*?</\1>", "", art.group(0) if art else html, flags=re.S)
    t = BLOCK.sub("\x00", t)                        # block tags = paragraph breaks
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("\n", " ")                        # Google hard-wraps inside <p>
    for ent, char in ENTITIES.items():
        t = t.replace(ent, char)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.split("\x00")]
    return "\n".join(ln for ln in lines if ln and not JUNK.match(ln))


def doc_diff(old, new):
    """Unified diff between the old and new doc snapshots ('' if unchanged)."""
    if old == new:
        return ""
    return "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(), "old", "new", n=0, lineterm=""))


def fetch():
    """Download the feed and return {incident_id: snapshot} for spam updates."""
    seen = {}
    for inc in json.loads(get(FEED)):
        if not SPAM.search(inc.get("external_desc", "")):
            continue
        seen[inc["id"]] = {
            "desc": inc["external_desc"],
            "begin": inc.get("begin"),
            "end": inc.get("end"),
            "updates": {
                u["when"]: re.sub(r"<([^>]+)>", r"\1", u["text"]).strip()
                for u in inc.get("updates", [])
            },
            "url": BASE + inc["id"],
        }
    return seen


def changes(old, new):
    """Structured list of everything in `new` that `old` didn't have."""
    out = []
    for iid, cur in sorted(new.items(), key=lambda kv: kv[1]["begin"] or "", reverse=True):
        prev = old.get(iid)
        base = {"desc": cur["desc"], "url": cur["url"]}
        if prev is None:
            out.append(dict(base, date=(cur["begin"] or "")[:10], kind="new",
                            text="\n".join(cur["updates"][w] for w in sorted(cur["updates"]))))
        else:
            for when, text in sorted(cur["updates"].items()):
                if when not in prev["updates"]:
                    out.append(dict(base, date=when[:10], kind="update", text=text))
            if cur.get("end") and not prev.get("end"):
                out.append(dict(base, date=cur["end"][:10], kind="done",
                                text="rollout finished " + cur["end"][:10]))
    return out


def fmt(c):
    if c["kind"] == "new":
        return "NEW  {}  ({})".format(c["desc"], c["date"])
    if c["kind"] == "done":
        return "DONE  {}: {}".format(c["desc"], c["text"])
    return "UPDATE  {}: {}".format(c["desc"], c["text"])


def diff(old, new):
    """Human-readable lines for everything in `new` that `old` didn't have."""
    return [fmt(c) for c in changes(old, new)]


def load_state(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def entry_md(title, sources, sections):
    """One changelog entry: title, source links, then fenced + / - blocks."""
    out = ["# " + title, ""]
    if sources:
        out += ["Sources:"] + ["- " + s for s in sources] + [""]
    for head, lang, body in sections:
        if body.strip():
            out += ["## " + head, "", "```" + lang, body.rstrip("\n"), "```", ""]
    return "\n".join(out) + "\n"


def append(root, entries):
    """{date: entry markdown} -> {repo path: full file content} for changelog/."""
    files = {}
    for date, md in entries.items():
        rel = "changelog/{}.md".format(date)
        try:
            with open(os.path.join(root, rel)) as fh:
                old = fh.read()
        except OSError:
            old = ""
        if md.strip() and md.strip() not in old:
            files[rel] = old + ("\n" if old else "") + md
    return files


def render(seen):
    """{repo path: markdown content} for the always-published files."""
    policies = ["# Google Search spam policies", "",
                "Auto-generated snapshot of <" + DOC + ">.", "",
                "Change history lives in [changelog/](changelog).", "",
                seen["doc"], ""]
    return {"spam-policies.md": "\n".join(policies)}


def publish(root, files, message):
    """Write changed files into the git repo at `root`, commit and push."""
    touched = []
    for rel, content in files.items():
        path = os.path.join(root, rel)
        try:
            with open(path) as fh:
                if fh.read() == content:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        touched.append(rel)
    if not touched or not os.path.isdir(os.path.join(root, ".git")):
        return touched
    for cmd in (["add", "-A"], ["commit", "-q", "-m", message],
                ["push", "-q", "--set-upstream", "origin", "HEAD"]):
        subprocess.run(["git", "-C", root] + cmd, check=True)
    return touched


def message(chs, dd):
    parts = []
    if chs:
        new = [c["desc"] for c in chs if c["kind"] == "new"]
        parts.append(new[0] if new else "rollout update")
    if dd:
        parts.append("spam policies +{}/-{}".format(
            *[sum(1 for ln in dd.splitlines() if ln.startswith(s) and not ln.startswith(s * 3))
              for s in ("+", "-")]))
    return "gspam: " + ", ".join(parts) if parts else "gspam: refresh"


def entries(chs, dd, doc_date=None):
    """{date: entry markdown} - one file per day, all changes grouped into it."""
    days = {}
    for c in chs:
        day = days.setdefault(c["date"], {"src": [], "sec": []})
        if c["url"] not in day["src"]:
            day["src"].append(c["url"])
        day["sec"].append((c["desc"], "text", "+ " + c["text"].replace("\n", "\n  ")))
    if dd:
        date = doc_date or time.strftime("%Y-%m-%d")
        days.setdefault(date, {"src": [], "sec": []})["sec"].append(
            ("Spam policies text diff", "diff", dd))
    return {d: entry_md(d, v["src"], v["sec"]) for d, v in sorted(days.items())}


def cdx_timestamps():
    """Distinct-content snapshot timestamps for the policy doc, oldest first."""
    url = ("https://web.archive.org/cdx/search/cdx?url="
           + urllib.parse.quote(DOC, safe="")
           + "&output=json&fl=timestamp&collapse=digest&filter=statuscode:200")
    return [row[0] for row in json.loads(get(url))[1:]]


def backfill(months, root):
    """Rebuild changelog/ from archive.org: last snapshot of each of the last N months."""
    picks = []
    for ts in reversed(cdx_timestamps()):
        if not picks or picks[-1][:6] != ts[:6]:
            picks.append(ts)
        if len(picks) >= months:
            break
    picks.reverse()
    print("backfill: {} archive snapshots, {} -> {}".format(
        len(picks), picks[0][:8], picks[-1][:8]))

    files, prev, prev_ts = {}, None, None
    for ts in picks:
        url = "https://web.archive.org/web/{}id_/{}".format(ts, DOC)
        try:
            text = doc_text(url)
        except Exception as exc:
            print("backfill: {} failed: {}".format(ts[:8], exc))
            time.sleep(3)
            continue
        if prev is not None and text != prev:
            date = ts[:4] + "-" + ts[4:6] + "-" + ts[6:8]
            dd = doc_diff(prev, text)
            files.update(append(root, {date: entry_md(
                date, [archive_url(prev_ts), archive_url(ts)],
                [("Text diff: {} -> {}".format(prev_ts[:8], ts[:8]), "diff", dd)])}))
            print("  {} +{}/-{}".format(date, *_counts(dd)))
        prev, prev_ts = text, ts
        time.sleep(1)
    return files


def archive_url(ts):
    return "https://web.archive.org/web/{}".format(ts)


def _counts(dd):
    return (sum(1 for ln in dd.splitlines() if ln.startswith("+") and not ln.startswith("+++")),
            sum(1 for ln in dd.splitlines() if ln.startswith("-") and not ln.startswith("---")))


def run(args):
    old = load_state(args.state)
    seen = {"incidents": fetch(), "doc": doc_text()}
    chs = changes(old.get("incidents", {}), seen["incidents"])
    dd = doc_diff(old["doc"], seen["doc"]) if "doc" in old else ""
    lines = [fmt(c) for c in chs]

    if args.json:
        print(json.dumps({"changes": lines, "doc_diff": dd}, indent=2))
    else:
        if not old:
            print("gspam: snapshot created ({} known spam updates, {} doc paragraphs)"
                  .format(len(seen["incidents"]), seen["doc"].count("\n") + 1))
        if lines:
            print("\n".join(lines))
        if dd:
            print("DOC  " + DOC)
            print(dd)

    os.makedirs(os.path.dirname(args.state) or ".", exist_ok=True)
    with open(args.state, "w") as fh:
        json.dump(seen, fh, indent=1)

    if args.publish:
        files = render(seen)
        files.update(append(args.publish, entries(chs, dd)))
        touched = publish(args.publish, files, message(chs, dd))
        print("gspam: published {}".format(", ".join(sorted(touched)) or "nothing new"))

    if args.backfill:
        root = args.publish or DEFAULT_ROOT
        touched = publish(root, backfill(args.backfill, root),
                          "gspam: backfill {} months of spam policies from archive.org".format(args.backfill))
        print("gspam: backfilled {}".format(", ".join(sorted(touched)) or "nothing new"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=DEFAULT_STATE, help="snapshot file")
    ap.add_argument("--watch", type=float, metavar="SEC", help="poll every SEC seconds")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--publish", metavar="REPO", nargs="?", const=DEFAULT_ROOT,
                    help="commit+push the markdown into this git repo (default: script dir)")
    ap.add_argument("--backfill", type=int, metavar="MONTHS",
                    help="rebuild changelog/ from N months of archive.org snapshots")
    args = ap.parse_args()

    while True:
        try:
            run(args)
        except Exception as exc:  # network hiccup: retry next tick, keep old snapshot
            print("gspam: fetch failed: {}".format(exc), file=sys.stderr)
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()