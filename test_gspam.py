import copy
import os
import subprocess
import tempfile

from gspam import diff, doc_diff, message, publish, render

SNAP = {
    "abc": {
        "desc": "September 2026 spam update",
        "begin": "2026-09-24T16:15:00+00:00",
        "end": None,
        "updates": {"t1": "Released the update."},
        "url": "https://status.search.google.com/incidents/abc",
    }
}

assert diff({}, SNAP)[0].startswith("NEW")          # first run announces it
assert diff({}, SNAP)[1] == "       Released the update."
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
assert "| Started | Update | Rollout | Status |" in md["CHANGELOG.md"]
assert "September 2026 spam update" in md["CHANGELOG.md"]
assert md["spam-policies.md"].endswith(DOC + "\n")

assert message([], "") == "gspam: refresh"
assert message(["NEW  October 2026 spam update  (2026-10-01)"], "") \
    == "gspam: October 2026 spam update"
assert message([], doc_diff("a", "b")) == "gspam: spam policies +1/-1"

with tempfile.TemporaryDirectory() as tmp:           # publish: commit once, then stay silent
    remote = os.path.join(tmp, "remote.git")
    subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
    subprocess.run(["git", "init", "-q", tmp], check=True)
    subprocess.run(["git", "-C", tmp, "remote", "add", "origin", remote], check=True)
    assert publish(tmp, md, "seed") == ["CHANGELOG.md", "spam-policies.md"]
    assert publish(tmp, md, "again") == []           # no diff -> no commit
    later = {"incidents": SNAP, "doc": DOC + "\nNew line."}
    assert publish(tmp, render(later), "doc change") == ["spam-policies.md"]
    log = subprocess.run(["git", "-C", tmp, "log", "--format=%s"],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert log == ["doc change", "seed"], log         # "again" left no empty commit

print("ok")