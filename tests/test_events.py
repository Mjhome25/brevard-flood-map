#!/usr/bin/env python3
"""Closure history invariants: nothing but a cleared event takes a record out of active, the history only grows,
and a reopened record shows green for exactly 24 hours. Runs on temporary files.  python3 -I tests/test_events.py <repo>
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import events as E  # noqa: E402

tmp = tempfile.mkdtemp(prefix="events-test-")
fails = 0
T0 = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)


def check(label, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + label)
    fails += not ok


def at(h):
    return E.iso(T0 + datetime.timedelta(hours=h))


def rep(rid, h, kind="reported", src="manual", **fields):
    f = {"status": "closed", "fullClosure": True, "where": "Babcock St at Palm Bay Rd", "lanes": "All lanes closed"}
    f.update(fields)
    return {"rid": rid, "kind": kind, "src": src, "actor": "test", "stime": at(h), "fields": f}


def fresh(name):
    p = os.path.join(tmp, name)
    if os.path.exists(p):
        os.remove(p)
    return p


def state(path, hours=0):
    return E.derive(E.load(path), T0 + datetime.timedelta(hours=hours))["records"]


A, B = "brevard:manual:babcock-palm-bay", "brevard:fl511:123"

# 1. Nothing but cleared ends active ---------------------------------------------------------------------------
p = fresh("a.jsonl")
E.append([rep(A, 0), rep(B, 0, src="fl511", status="flooded", fullClosure=True)], p, now=T0)
noise = [
    {"rid": B, "kind": "missing", "src": "fl511", "actor": "test", "stime": at(1)},
    {"kind": "source", "src": "fl511", "state": "down", "actor": "test", "stime": at(2)},
    {"kind": "source", "src": "fl511", "state": "empty", "actor": "test", "stime": at(3)},
    {"rid": A, "kind": "recheck", "src": "manual", "reason": "traffic flowing normally", "actor": "test", "stime": at(4)},
    rep(A, 5, kind="relisted", status="caution", fullClosure=False, lanes="Roadway open"),
    rep(B, 6, kind="relisted", src="fl511", status="caution", fullClosure=False, lanes="1 lane open"),
]
E.append(noise, p, now=T0 + datetime.timedelta(hours=6))
for hours in (1, 24, 72, 24 * 1000):
    s = state(p, hours)
    check(f"after missing, source down/empty, recheck and downgrades, {hours} h later: both still active",
          s[A]["status"] == "active" and s[B]["status"] == "active")
s = state(p, 7)
check("a feed downgrade keeps closed + fullClosure and the old lanes text",
      s[A]["fields"]["status"] == "closed" and s[A]["fields"]["fullClosure"] and s[A]["fields"]["lanes"] == "All lanes closed")
check("a feed downgrade flags 'source shows reduced impact'", "source shows reduced impact" in s[A]["recheck"])
m = E.derive(E.load(p)[:3], T0 + datetime.timedelta(hours=2))["records"][B]
check("missing is recorded, and the record shows awaiting official reopening",
      bool(m["missingSince"]) and m["awaitingOfficial"] and m["status"] == "active")
check("listed again after missing clears missingSince but stays flagged by its downgrade",
      s[B]["missingSince"] is None and s[B]["awaitingOfficial"])
check("over 72 h without a new report shows awaiting official reopening", state(p, 80)[A]["awaitingOfficial"])

# 2. Upward changes are kept ------------------------------------------------------------------------------------
p = fresh("b.jsonl")
E.append([rep(A, 0, status="flooded", fullClosure=False, lanes="Water on road")], p, now=T0)
E.append([rep(A, 2, kind="relisted", status="closed", fullClosure=True, lanes="All lanes closed",
              where="somewhere else")], p, now=T0)
s = state(p, 3)[A]
check("a relisted with higher severity raises status, fullClosure and lanes",
      s["fields"]["status"] == "closed" and s["fields"]["fullClosure"] and s["fields"]["lanes"] == "All lanes closed")
check("a relisted never moves the record (where unchanged)", s["fields"]["where"] == "Babcock St at Palm Bay Rd")
check("lastReported follows the newest source time", s["lastReported"] == at(2))
E.append([rep(A, 1, kind="relisted", status="closed", fullClosure=True)], p, now=T0)
check("an older relisted does not move lastReported back", state(p, 3)[A]["lastReported"] == at(2))

# 3. Idempotency ------------------------------------------------------------------------------------------------
p = fresh("c.jsonl")
first = E.append([rep(A, 0)], p, now=T0)
again = E.append([rep(A, 0)], p, now=T0 + datetime.timedelta(hours=1))
check("the same observation twice is written once", len(first) == 1 and again == [] and len(E.load(p)) == 1)
changed = E.append([rep(A, 0, lanes="Both directions closed")], p, now=T0)
check("same source time with changed content is a new event", len(changed) == 1)

# 4. No cleared issuance path while clearance is disabled -------------------------------------------------------
p = fresh("d.jsonl")
E.append([rep(A, 0)], p, now=T0)
try:
    E.append([{"rid": A, "kind": "cleared", "src": "manual", "actor": "test", "stime": at(1),
               "evidence": {"reopenedAt": at(1)}}], p)
    check("append refuses a cleared event", False)
except E.Refused:
    check("append refuses a cleared event", True)
check("the refused cleared left the record active", state(p, 2)[A]["status"] == "active")

# 5. check_events: prefix, schema, cleared ----------------------------------------------------------------------
county = os.path.join(repo, "county.json")


def run_check(before, after):
    r = subprocess.run([sys.executable, "-I", os.path.join(repo, "check_events.py"), "--before", before,
                        "--events", after, "--county", county], capture_output=True, text=True, cwd=repo)
    return r.returncode, r.stderr


p = fresh("e.jsonl")
E.append([rep(A, 0), rep(B, 0, src="fl511")], p, now=T0)
before = os.path.join(tmp, "e.before")
shutil.copy(p, before)
E.append([rep(A, 3, kind="relisted")], p, now=T0)
check("check_events passes a history that only grew", run_check(before, p)[0] == 0)

lines = open(p).read().splitlines()
edited = os.path.join(tmp, "e.edited")
open(edited, "w").write("\n".join([lines[0].replace("Babcock", "Minton")] + lines[1:]) + "\n")
rc, err = run_check(before, edited)
check("check_events fails when an earlier line is edited", rc == 1 and "unchanged prefix" in err)
open(edited, "w").write("\n".join(lines[1:]) + "\n")
rc, err = run_check(before, edited)
check("check_events fails when an earlier line is removed", rc == 1 and "unchanged prefix" in err)
open(edited, "w").write(open(p).read() + "{not json\n")
rc, err = run_check(before, edited)
check("check_events fails on a malformed line", rc == 1 and "unreadable" in err)
dup = json.loads(lines[-1])
dup["seq"] = len(lines) + 1
open(edited, "w").write(open(p).read() + E.canon(dup) + "\n")
rc, err = run_check(before, edited)
check("check_events fails on a duplicate eid", rc == 1 and "duplicate eid" in err)
forged = {"rid": A, "kind": "cleared", "src": "manual", "actor": "someone", "stime": at(4),
          "evidence": {"reopenedAt": at(4), "url": "https://example.com"}, "seq": len(lines) + 1, "at": at(4)}
forged["eid"] = E.eid_of(forged)
open(edited, "w").write(open(p).read() + E.canon(forged) + "\n")
rc, err = run_check(before, edited)
check("check_events fails on a hand-written cleared event while clearance is disabled", rc == 1 and "disabled" in err)

# 6. Reducer behaviour for an accepted reopening (replay of a cleared event built directly, not via append) ------
evs = E.load(fresh("f.jsonl")) + [dict(rep(A, 0), seq=1, at=at(0))]
evs.append({"seq": 2, "at": at(10), "rid": A, "kind": "cleared", "src": "manual", "stime": at(10),
            "evidence": {"reopenedAt": at(10)}})
d = lambda h, ev=evs: E.derive(ev, T0 + datetime.timedelta(hours=h))["records"][A]
check("a reopened record is green just before 24 h", d(10 + 23.99)["show"] == "green")
check("and hidden at exactly 24 h, with its history kept", d(34)["show"] == "hidden" and d(34)["evidence"])
evs_old = evs + [dict(rep(A, 9, kind="relisted"), seq=3, at=at(11))]
check("a relisted older than the reopening does not reactivate", d(12, evs_old)["status"] == "reopened")
evs_new = evs + [dict(rep(A, 12, kind="relisted"), seq=3, at=at(12))]
check("a newer report after the reopening turns it red again", d(13, evs_new)["status"] == "active")
check("replay never extends green past reopenedAt + 24 h", d(10 + 24 + 100)["show"] == "hidden")

print("failures:", fails)
sys.exit(1 if fails else 0)
