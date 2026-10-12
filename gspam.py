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
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.error
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
SITE = "https://developers.google.com"
BLOG = SITE + "/search/blog"
# slug-only matching is too loose (shipping "policies", "return policies")
SPAM_POST = re.compile(r"spam|abuse|hijack|enforcement|site reputation", re.I)
BLOG_INDEX = re.compile(
    r'href="(/search/blog/(\d{4})/(\d{2})/[^"#]+)"[^>]*class="devsite-nav-title"'
    r'[^>]*>\s*<span class="devsite-nav-text" tooltip>([^<]+)</span>', re.S)
POST_DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})$", re.M)
JUNK = re.compile(r"^(Home|Search Central|Documentation|Send feedback|Previous|Next"
                  r"|Was this helpful\??|Last updated .*)$", re.I)
BLOCK = re.compile(r"</?(p|li|h[1-6]|tr|dt|dd|blockquote|pre|ul|ol|table|div)\b[^>]*>", re.I)
HEADING = re.compile(r"<h[1-6]\b[^>]*>", re.I)      # opening tag only
ENTITIES = {"&amp;": "&", "&nbsp;": " ", "&#39;": "'", "&quot;": '"',
            "&lt;": "<", "&gt;": ">", "&rsquo;": "'", "&hellip;": "..."}


def get(url, tries=5):
    """Fetch a URL. archive.org throttles hard, so back off and retry on 5xx/timeouts."""
    req = urllib.request.Request(url, headers={"user-agent": "gspam/1"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                enc = resp.headers.get("Content-Encoding", "")
            break
        except urllib.error.HTTPError as exc:
            if attempt == tries - 1 or exc.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(10 * (attempt + 1))
        except (urllib.error.URLError, OSError):
            # "Connection refused" is archive.org rate limiting us
            if attempt == tries - 1:
                raise
            time.sleep(15 * (attempt + 1))
    if enc == "gzip":
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8", "replace")


CACHE = os.path.join(os.path.expanduser("~/.cache"), "gspam")


def cached_doc(url, key):
    """Parse a doc URL, reusing the on-disk snapshot so re-runs cost no requests."""
    path = os.path.join(CACHE, key + ".txt")
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        pass
    text = doc_text(url)
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return text


def doc_text(url=DOC):
    """Return the spam policies doc as one paragraph per line, boilerplate stripped."""
    html = get(url)
    art = re.search(r"<article.*?</article>", html, re.S)
    t = re.sub(r"<(script|style|svg)\b.*?</\1>", "", art.group(0) if art else html, flags=re.S)
    t = HEADING.sub("\x01", t)                      # mark headings, keep their level
    t = BLOCK.sub("\x00", t)                        # block tags = paragraph breaks
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("\n", " ")                        # Google hard-wraps inside <p>
    for ent, char in ENTITIES.items():
        t = t.replace(ent, char)
    lines = []
    for chunk in t.split("\x00"):
        # text before a heading marker is body copy; only the piece right after it is the title
        pieces = chunk.split("\x01")
        for i, piece in enumerate(pieces):
            piece = re.sub(r"\s+", " ", piece).strip()
            if piece and not JUNK.match(piece):
                lines.append(("## " + piece) if i else piece)
    return "\n".join(ln for ln in lines if ln)


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


def blog_index():
    """{post path: (title, YYYY-MM)} for spam-related Search Central blog posts."""
    out = {}
    for path, year, month, title in BLOG_INDEX.findall(get(BLOG)):
        title = html.unescape(title).strip()
        if SPAM_POST.search(title):
            out[path] = (title, "{}-{}".format(year, month))
    return out


def post_change(path, meta):
    """Fetch a blog post and shape it like any other change."""
    title, month = meta
    body = doc_text(SITE + path)
    match = POST_DATE.search(body)
    date = month + "-01"
    if match:
        try:
            date = time.strftime("%Y-%m-%d", time.strptime(match.group(1), "%B %d, %Y"))
        except ValueError:
            pass
    text = "\n".join(ln for ln in body.split("\n")
                     if not ln.startswith("## ")
                     and ln not in (match.group(1) if match else date, "Google Search Central Blog"))
    return {"date": date, "kind": "post", "desc": title, "url": SITE + path, "text": text.strip()}


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
    if c["kind"] == "post":
        return "POST  {}  ({})".format(c["desc"], c["date"])
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


WRAP = 100


def wrap_block(body, width=WRAP):
    """Hard-wrap long fenced lines to fit a code box; tails keep the leading marker.

    Lines that do not start with a +/-/space marker are wrapped flush left.
    """
    out = []
    for line in body.splitlines():
        if len(line) <= width or line.startswith(("@@", "--- ", "+++ ")):
            out.append(line)
            continue
        if line[0] in "+- ":
            chunks = textwrap.wrap(line[1:], width=width - 3) or [""]
            out.append(line[:1] + chunks[0])
            out += ["   " + c for c in chunks[1:]]
        else:
            out += textwrap.wrap(line, width=width) or [""]
    return "\n".join(out)


def entry_md(title, sources, sections):
    """One changelog entry: title, source links, then fenced + / - blocks."""
    out = ["# " + title, ""]
    if sources:
        out += ["Sources:"] + ["- " + s for s in sources] + [""]
    for head, lang, body in sections:
        if body.strip():
            out += ["## " + head, "", "```" + lang,
                    wrap_block(body).rstrip("\n"), "```", ""]
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
    out = ["# Google Search spam policies", "",
           "Auto-generated snapshot of <" + DOC + ">.", "",
           "Change history lives in [changelog/](changelog).", ""]
    for para in seen["doc"].split("\n"):
        out += [""] + textwrap.wrap(para, width=WRAP)
    return {"spam-policies.md": "\n".join(out) + "\n"}


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
        posts = sum(1 for c in chs if c["kind"] == "post")
        if new:
            parts.append(new[0])
        elif not posts:
            parts.append("rollout update")
        if posts:
            parts.append("{} blog post(s)".format(posts))
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
    """Rebuild changelog/ from archive.org: month-end snapshots plus every
    snapshot bracketing a known spam update date, so a policy change gets
    attributed to the update it rode along with rather than to a month end."""
    all_ts = cdx_timestamps()
    cutoff = all_ts[-months * 31:] if months else all_ts
    picks = []
    for ts in reversed(cutoff):                 # last snapshot of each month
        if not picks or picks[-1][:6] != ts[:6]:
            picks.append(ts)
    anchor = {d for inc in fetch().values() for d in
              ((inc.get("begin") or "")[:10], (inc.get("end") or "")[:10]) if d}
    for date in sorted(anchor):
        day = date.replace("-", "")
        before = [t for t in all_ts if t[:8] <= day]
        after = [t for t in all_ts if t[:8] >= day]
        picks += [before[-1] if before else None, after[0] if after else None]
    picks = sorted(set(t for t in picks if t))
    print("backfill: {} snapshots over {} months, {} anchors, {} -> {}".format(
        len(picks), months, len(anchor), picks[0][:8], picks[-1][:8]), flush=True)

    missing = fetch_snapshots(picks, gap=2)
    for attempt in range(2):          # archive.org throttles hard; cached hits are free
        if not missing:
            break
        print("backfill: retrying {} after a pause".format(len(missing)), flush=True)
        time.sleep(60 * (attempt + 1))
        missing = fetch_snapshots(missing, gap=10)

    entries_by_day, prev, prev_ts = {}, None, None
    for ts in picks:
        try:
            text = cached_doc(archive_url(ts, raw=True), ts)
        except OSError:
            print("  {} never fetched, skipping".format(ts[:8]), flush=True)
            prev = prev_ts = None
            continue
        if prev is not None and text != prev:
            date = "{}-{}-{}".format(ts[:4], ts[4:6], ts[6:8])
            dd = doc_diff(prev, text)
            md = entry_md(date, [archive_url(prev_ts), archive_url(ts)],
                          [("Text diff: {} -> {}".format(prev_ts[:8], ts[:8]), "diff", dd)])
            entries_by_day.setdefault(date, "")
            if md.strip() not in entries_by_day[date]:
                entries_by_day[date] += md
            print("  {} +{}/-{}".format(date, *_counts(dd)), flush=True)
        prev, prev_ts = text, ts
    return append(root, entries_by_day)


def fetch_snapshots(timestamps, gap):
    """Download and cache each snapshot; returns the ones still unavailable."""
    missing = []
    for ts in timestamps:
        try:
            cached_doc(archive_url(ts, raw=True), ts)
        except Exception as exc:
            print("  {} failed: {}".format(ts[:8], exc), flush=True)
            missing.append(ts)
            continue
        time.sleep(gap)
    return missing


def archive_url(ts, raw=False):
    """Snapshot permalink; raw=True skips Wayback's HTML rewriter."""
    return "https://web.archive.org/web/{}{}".format(ts, "id_/" + DOC if raw else "")


def _counts(dd):
    return (sum(1 for ln in dd.splitlines() if ln.startswith("+") and not ln.startswith("+++")),
            sum(1 for ln in dd.splitlines() if ln.startswith("-") and not ln.startswith("---")))


def index_md(root, extra):
    """changelog/README.md: every entry on disk plus the ones about to be written."""
    folder = os.path.join(root, "changelog")
    days = {}
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if name.endswith(".md") and name != "README.md":
            with open(os.path.join(folder, name)) as fh:
                days[name[:-3]] = fh.read()
    days.update(extra)
    out = ["# Spam changelog", "",
           "One file per day, oldest first. Backfilled from archive.org, "
           "kept current by the scheduled run.", ""]
    for date in sorted(days):
        titles = re.findall(r"^## (.+)$", days[date], re.M)
        out.append("- [{}]({}.md) — {}".format(
            date, date, "; ".join(dict.fromkeys(titles)) or "no content change"))
    return "\n".join(out) + "\n"


def run(args):
    old = load_state(args.state)
    seen = {"incidents": fetch(), "doc": doc_text()}
    chs = changes(old.get("incidents", {}), seen["incidents"])
    posts = blog_index()
    fresh = sorted(p for p in posts if p not in old.get("posts", {}))
    chs += [post_change(p, posts[p]) for p in fresh]
    seen["posts"] = {p: posts[p][1] for p in posts}
    dd = doc_diff(old["doc"], seen["doc"]) if "doc" in old else ""
    lines = [fmt(c) for c in chs]

    if args.json:
        print(json.dumps({"changes": lines, "doc_diff": dd}, indent=2))
    else:
        if not old:
            print("gspam: snapshot created ({} spam updates, {} blog posts, {} doc paragraphs)"
                  .format(len(seen["incidents"]), len(seen["posts"]), seen["doc"].count("\n") + 1))
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
        ent = entries(chs, dd)
        files.update(append(args.publish, ent))
        files["changelog/README.md"] = index_md(args.publish, ent)
        touched = publish(args.publish, files, message(chs, dd))
        print("gspam: published {}".format(", ".join(sorted(touched)) or "nothing new"))

    if args.backfill:
        root = args.publish or DEFAULT_ROOT
        files = backfill(args.backfill, root)
        files["changelog/README.md"] = index_md(root, {})
        touched = publish(root, files,
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