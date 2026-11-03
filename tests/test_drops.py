#!/usr/bin/env python3
"""The FHP/FL511 drop trial: every observed feed drop, every report at that place that says it was wrong; read-only."""
import datetime
import json
import os
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import drops as D  # noqa: E402
import events as E  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok  " if ok else "FAIL"), name)
    fails += not ok


tmp = tempfile.mkdtemp()
NOW = datetime.datetime(2026, 11, 4, 16, 0, tzinfo=datetime.timezone.utc)
FL = {"source": "FL511 (FDOT)", "sourceUrl": "https://fl511.com/", "road": "Wickham Rd", "where": "Wickham Rd at Pineda Cswy",
      "title": "Flooding", "status": "closed", "fullClosure": True, "lat": 28.2, "lng": -80.7}
CRASH = dict(FL, source="FHP live CAD", sourceUrl=E.FHP_URL, road="US-1", where="US-1 at Viera Blvd", title="MVC",
             status="mvc", fullClosure=False)


def ev(kind, rid, at, src="fl511", **kw):
    return dict(kind=kind, rid=rid, src=src, at=at, actor="t", stime=kw.pop("stime", at), **kw)


def seen(rid, at, fields=FL, src="fl511"):
    return ev("reported", rid, at, src, fields=fields)


def gone(rid, at, src="fl511"):
    return ev("missing", rid, at, src)


def rep(events, prev=None, review=None, now=NOW):
    p = os.path.join(tmp, "e.jsonl")
    open(p, "w").write("".join(json.dumps(dict(e, eid=f"e{i}")) + "\n" for i, e in enumerate(events)))
    before = open(p).read()
    out = D.report(now, p, prev, review)
    assert open(p).read() == before, "the drop trial wrote the closure history"
    return out


A = "brevard:fl511:1"
base = [seen(A, "2026-11-04T10:00:00Z"), gone(A, "2026-11-04T12:00:00Z")]
r = rep(base)
check("one observed FL511 drop in the week is one trial episode with nothing found (counted, never called proof)",
      r["trial"]["episodes"] == 1 and r["trial"]["nothing found"] == 1 and r["cases"][0]["finding"] == "nothing found"
      and "not proof" in r["note"] and r["forMike"] == [])
early = [seen(A, "2026-11-01T10:00:00Z"), gone(A, "2026-11-01T12:00:00Z")]
late = [seen("brevard:fl511:2", "2026-11-12T10:00:00Z"), gone("brevard:fl511:2", "2026-11-12T12:00:00Z")]
r = rep(early + late, now=datetime.datetime(2026, 11, 13, tzinfo=datetime.timezone.utc))
check("drops before Mike's decision are the baseline and drops after the week are outside it; neither is in the trial",
      r["trial"]["episodes"] == 0 and r["baseline"]["episodes"] == 1 and [c["cohort"] for c in r["cases"]] == ["baseline", "after"])
twice = base + [ev("relisted", A, "2026-11-04T13:00:00Z", fields=FL), gone(A, "2026-11-04T14:00:00Z")]
r = rep(twice)
check("two separate disappearances of one incident are two episodes (one incident), and the relisting is contrary evidence",
      r["trial"]["episodes"] == 2 and r["trial"]["incidents"] == 1
      and r["cases"][0]["counterparts"][0]["relation"] == "source-relisted")
C = "brevard:fhp:9"
crash = [seen(C, "2026-11-04T10:00:00Z", CRASH, "fhp"), gone(C, "2026-11-04T11:00:00Z", "fhp"),
         ev("cleared", C, "2026-11-04T11:30:00Z", "fhp", evidence={"kind": "fhp-dropped", "agency": "Florida Highway Patrol",
                                                                    "url": E.FHP_URL, "missingSince": "2026-11-04T11:00:00Z",
                                                                    "reopenedAt": "2026-11-04T11:30:00Z"})]
r = rep(crash)
check("an FHP crash's disappearance and its retirement are one episode, not two",
      r["trial"]["episodes"] == 1 and r["trial"]["bySource"] == {"fhp": 1, "fl511": 0} and r["cases"][0]["group"] == "crash, state road")
off = [seen(A, "2026-11-04T10:00:00Z"),
       ev("cleared", A, "2026-11-04T14:00:00Z", evidence={"kind": "official", "url": "https://www.melbourneflorida.org/x",
                                                          "quote": "Wickham Road is open.", "publishedDate": "2026-11-04",
                                                          "sourceTime": "2026-11-04T13:30:00Z",
                                                          "reopenedAt": "2026-11-04T13:30:00Z"})]
check("an official reopening of a feed report is not a feed drop", rep(off)["trial"]["episodes"] == 0)
for st, full, why in (("flooded", True, "flooded with a full closure"), ("closed", False, "closed without the full flag")):
    other = dict(FL, source="City of Melbourne", sourceUrl="https://www.melbourneflorida.org/y", status=st, fullClosure=full)
    r = rep(base + [ev("reported", "brevard:manual:w", "2026-11-04T10:00:00Z", "manual", fields=other),
                    ev("relisted", "brevard:manual:w", "2026-11-04T13:00:00Z", "manual", fields=other)])
    cp = r["cases"][0]["counterparts"]
    check(f"Codex repro: a city report {why} at the same place, reported after the drop, is contrary evidence with its link",
          r["cases"][0]["finding"] == "contrary evidence" and cp and cp[0]["relation"] == "reported-after-drop"
          and cp[0]["url"] == "https://www.melbourneflorida.org/y" and cp[0]["listing"].startswith("not a live feed"))
stale = dict(FL, source="WESH 2", sourceUrl="https://example.org/wesh")
r = rep([ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base)
check("an older entry from another source still on the map is a possibly stale entry, listed for Mike with its times",
      r["cases"][0]["finding"] == "possibly stale entry" and r["forMike"] == [r["cases"][0]["id"]]
      and r["cases"][0]["counterparts"][0]["lastReported"] == "2026-11-03T16:00:00Z")
caution = dict(FL, source="WESH 2", status="caution", fullClosure=False, title="Shoulder blocked")
r = rep([ev("reported", "brevard:manual:c", "2026-11-04T13:00:00Z", "manual", fields=caution)] + base)
check("a lesser report (a caution where the drop was a closure) is listed apart, not as contrary evidence",
      r["cases"][0]["finding"] == "nothing found" and r["cases"][0]["lesser"][0]["rid"] == "brevard:manual:c")
far = dict(stale, where="Wickham Rd at Eau Gallie Blvd")
check("a report at a different cross street is not a counterpart",
      rep([ev("reported", "brevard:manual:f", "2026-11-03T16:00:00Z", "manual", fields=far)] + base)["cases"][0]["counterparts"] == [])
first = rep([ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base)
again = rep([ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base, prev=first)
check("a finding is new for Mike once; the next run with the same evidence does not repeat it",
      first["newForMike"] == [first["cases"][0]["id"]] and again["newForMike"] == [] and again["forMike"] == first["forMike"]
      and again["cases"][0]["firstFound"] == first["cases"][0]["firstFound"])
more = rep([ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base
           + [ev("reported", "brevard:manual:n", "2026-11-04T15:00:00Z", "manual", fields=stale)], prev=again)
check("new evidence for a finding already shown makes it changed and brings it back", more["newForMike"] == [more["cases"][0]["id"]])
done = rep([ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base,
           review={first["cases"][0]["id"]: {"decision": "stale entry, ok"}})
check("a finding Mike has reviewed is not brought to him again", done["forMike"] == [] and done["cases"][0]["review"] == "stale entry, ok")
check("the ledger line says nothing found is not proof", "nothing found (not proof)" in D.line(rep(base)))
r = rep(base + [seen("brevard:fl511:3", "2026-11-04T10:00:00Z", dict(FL, road="Minton Rd", where="Minton Rd at Norfolk Pkwy",
                                                                      status="flooded", fullClosure=False)),
                gone("brevard:fl511:3", "2026-11-04T12:00:00Z")])
check("the trial is split by crash or flooding/hazard and state road or local street",
      set(r["trial"]["byGroup"]) == {"flooding/hazard, local street"} and r["trial"]["byGroup"]["flooding/hazard, local street"]["episodes"] == 2)

print("failures:", fails)
sys.exit(1 if fails else 0)
