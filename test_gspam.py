import copy
import os
import subprocess
import tempfile

from gspam import (append, changes, diff, doc_diff, entry_md, entries, fmt, index_md, message,
                   publish, render, wrap_block)

SNAP = {
    "abc": {
        "desc": "September 2026 spam update",
        "begin": "2026-09-24T16:15:00+00:00",
        "end": None,
        "updates": {"t1": "Released the update."},
        "url": "https://status.search.google.com/incidents/abc",
    }
}

assert diff({}, SNAP)[0] == "NEW  September 2026 spam update  (2026-09-24)"
assert changes({}, SNAP)[0]["date"] == "2026-09-24"
assert diff(SNAP, SNAP) == []                       # unchanged -> silent

rolled = copy.deepcopy(SNAP)
rolled["abc"]["updates"]["t2"] = "Rollout complete."
rolled["abc"]["end"] = "2026-10-08T08:00:00+00:00"
lines = diff(SNAP, rolled)
assert lines == [
    "UPDATE  September 2026 spam update: Rollout complete.",
    "DONE  September 2026 spam update: rollout finished 2026-10-08",
], lines

later = copy.deepcopy(rolled)
later["def"] = dict(SNAP["abc"], desc="October 2026 spam update")
assert diff(rolled, later)[0].startswith("NEW  October")

DOC = "## Scaled content abuse\nTry to game ranking."
assert doc_diff(DOC, DOC) == ""                     # unchanged -> silent
d = doc_diff(DOC, "## Scaled content abuse\nA brand new policy.")
assert d.splitlines()[0] == "--- old" and d.splitlines()[1] == "+++ new", d
assert "-Try to game ranking." in d and "+A brand new policy." in d, d

DOC = "## Scaled content abuse\nTry to game ranking."
STATE = {"incidents": SNAP, "doc": DOC}
md = render(STATE)
pol = md["spam-policies.md"]
assert "changelog" in pol and pol.endswith("\n")
assert "\n## Scaled content abuse\n" in pol                          # heading kept
assert "Try to game ranking." in pol
assert all(len(ln) <= 100 for ln in pol.split("\n")), max(len(ln) for ln in pol.split("\n"))

assert message([], "") == "gspam: refresh"
assert message(changes({}, SNAP), "") == "gspam: September 2026 spam update"
assert message([], doc_diff("a", "b")) == "gspam: spam policies +1/-1"

md = entry_md("2026-09-24", ["https://x"], [("T", "diff", "-a\n+b"), ("skip", "diff", "  ")])
assert md.startswith("# 2026-09-24\n") and "```diff\n-a\n+b\n```" in md and "skip" not in md

POST = {"date": "2026-04-13", "kind": "post", "desc": "New spam policy",
        "url": "https://developers.google.com/search/blog/2026/04/x", "text": "Body line."}
pe = entries([POST], "")["2026-04-13"]
assert "## New spam policy" in pe and "+ Body line." in pe
assert "developers.google.com/search/blog/2026/04/x" in pe
assert message([POST], "") == "gspam: 1 blog post(s)"
assert diff({}, {}) == [] and fmt(POST) == "POST  New spam policy  (2026-04-13)"

LONG = "- " + " ".join(["spamword"] * 40)
w = wrap_block(LONG)
assert max(len(ln) for ln in w.split("\n")) <= 100, max(len(ln) for ln in w.split("\n"))
assert w.startswith("- ") and "\n   " in w            # marker kept, tails indented
assert " ".join(w.split()) == LONG                     # no words lost or added
assert wrap_block("@@ -1,2 +1,2 @@\n- ok") == "@@ -1,2 +1,2 @@\n- ok"   # headers untouched
assert wrap_block("+ " + "x " * 80).split("\n")[0].startswith("+ ")
# announcement text: no marker, wraps flush left, still fits
t = wrap_block("Released the update, which applies globally. " * 6)
assert max(len(ln) for ln in t.split("\n")) <= 100 and all(not ln[0].isspace() for ln in t.split("\n"))
assert "Released the update" in t
assert t.split() == ("Released the update, which applies globally. " * 6).split()  # nothing lost
assert wrap_block("word " * 300) == wrap_block("word " * 300)          # deterministic

e = entries(changes({}, SNAP), "", doc_date=None)
key = "2026-09-24"
assert e[key].startswith("# 2026-09-24") and "+ Released the update." in e[key]

idx = index_md("/nonexistent", e)
assert "- [2026-09-24](2026-09-24.md) — September 2026 spam update" in idx, idx
assert index_md("/nonexistent", {}).count("- [") == 0
same_day = entries(changes({}, SNAP) + [dict(changes({}, SNAP)[0], date="2026-10-08", kind="done",
                                             text="rollout finished 2026-10-08")], "", "2026-10-08")
assert sorted(same_day) == ["2026-09-24", "2026-10-08"]   # two dates -> two files
assert same_day["2026-10-08"].startswith("# 2026-10-08")

with tempfile.TemporaryDirectory() as tmp:
    files = append(tmp, e)                          # append computes; publish writes
    assert set(files) == {"changelog/2026-09-24.md"}
    assert set(append(tmp, e)) == set(files)         # content still missing on disk
    publish(tmp, files, "x")                        # not a git repo -> write only, no commit
    assert append(tmp, e) == {}                     # now on disk -> nothing to write
    both = append(tmp, entries([], "-old\n+new", "2026-10-08"))
    assert set(both) == {"changelog/2026-10-08.md"}
    assert "-old\n+new" in both["changelog/2026-10-08.md"]

with tempfile.TemporaryDirectory() as tmp:           # publish: commit once, then stay silent
    remote = os.path.join(tmp, "remote.git")
    subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
    subprocess.run(["git", "init", "-q", tmp], check=True)
    subprocess.run(["git", "-C", tmp, "remote", "add", "origin", remote], check=True)
    files = dict(render(STATE))
    files.update(append(tmp, e))
    assert sorted(publish(tmp, files, "seed")) == ["changelog/2026-09-24.md", "spam-policies.md"]
    assert publish(tmp, files, "again") == []       # no diff -> no commit
    later = {"incidents": SNAP, "doc": DOC + "\nNew line."}
    assert publish(tmp, render(later), "doc change") == ["spam-policies.md"]
    log = subprocess.run(["git", "-C", tmp, "log", "--format=%s"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert log == ["doc change", "seed"], log         # "again" left no empty commit

print("ok")