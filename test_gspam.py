import copy
import os
import subprocess
import tempfile

from gspam import append, changes, diff, doc_diff, entry_md, entries, message, publish, render

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

DOC = "Scaled content abuse\nTry to game ranking."
assert doc_diff(DOC, DOC) == ""                     # unchanged -> silent
d = doc_diff(DOC, "Scaled content abuse\nA brand new policy.")
assert d.splitlines()[0] == "--- old" and d.splitlines()[1] == "+++ new", d
assert "-Try to game ranking." in d and "+A brand new policy." in d, d

STATE = {"incidents": SNAP, "doc": DOC}
md = render(STATE)
assert md["spam-policies.md"].endswith(DOC + "\n")
assert "changelog" in md["spam-policies.md"]

assert message([], "") == "gspam: refresh"
assert message(changes({}, SNAP), "") == "gspam: September 2026 spam update"
assert message([], doc_diff("a", "b")) == "gspam: spam policies +1/-1"

md = entry_md("2026-09-24", ["https://x"], [("T", "diff", "-a\n+b"), ("skip", "diff", "  ")])
assert md.startswith("# 2026-09-24\n") and "```diff\n-a\n+b\n```" in md and "skip" not in md

e = entries(changes({}, SNAP), "", doc_date=None)
key = "2026-09-24"
assert e[key].startswith("# 2026-09-24") and "+ Released the update." in e[key]
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