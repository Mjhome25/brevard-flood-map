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


def rep(events, prev=None, review=None, now=NOW, relayed=None):
    p = os.path.join(tmp, "e.jsonl")
    open(p, "w").write("".join(json.dumps(dict(e, eid=f"e{i}")) + "\n" for i, e in enumerate(events)))
    before = open(p).read()
    out = D.report(now, p, prev, review, relayed)
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
S = [ev("reported", "brevard:manual:s", "2026-11-03T16:00:00Z", "manual", fields=stale)] + base
first = rep(S)
cid, fp = first["cases"][0]["id"], first["cases"][0]["fingerprint"]
check("a finding stays new for Mike until it is relayed in chat (a missed run never loses it)",
      first["newForMike"] == [cid] and rep(S, prev=first)["newForMike"] == [cid])
told = rep(S, prev=first, relayed={cid: fp})
check("once relayed, the same evidence is not brought again but stays in the standing queue until reviewed",
      told["newForMike"] == [] and told["forMike"] == [cid] and told["cases"][0]["firstFound"] == first["cases"][0]["firstFound"])
more = S + [ev("reported", "brevard:manual:n", "2026-11-04T15:00:00Z", "manual", fields=dict(stale, source="City of Melbourne"))]
check("new evidence for a relayed finding brings it back", rep(more, relayed={cid: fp})["newForMike"] == [cid])
done = rep(S, review={cid: {"decision": "stale entry, ok", "fingerprint": fp}})
check("a finding Mike reviewed (for that evidence) leaves the queue", done["forMike"] == [] and done["cases"][0]["review"] == "stale entry, ok")
again = rep(more, review={cid: {"decision": "stale entry, ok", "fingerprint": fp}})
check("Codex repro: reviewed as stale, then a NEW city closure after the drop -> contrary evidence, back in the queue",
      again["cases"][0]["finding"] == "contrary evidence" and again["forMike"] == [cid]
      and again["cases"][0]["review"] == "unreviewed (evidence changed since review)")
cp = first["cases"][0]["counterparts"]
check("a material change to the same counterpart (closed -> flooded, a newer report, listing) changes the fingerprint",
      all(D.fingerprint([dict(cp[0], **chg)]) != fp for chg in ({"status": "flooded"}, {"fullClosure": False},
                                                                {"lastReported": "2026-11-04T09:00:00Z"},
                                                                {"listing": "no longer listed by its feed"})))
fl = dict(FL, source="FL511 (FDOT)")
down = S[:1] + [ev("reported", "brevard:fl511:9", "2026-11-03T16:00:00Z", fields=fl)] + base \
    + [{"kind": "source", "rid": None, "src": "fl511", "at": "2026-11-04T15:00:00Z", "actor": "t", "state": "down"}]
try:
    lst = [c["listing"] for c in rep(down)["cases"][0]["counterparts"] if c["rid"] == "brevard:fl511:9"]
except Exception as exc:  # the source-health event shape is owned by events.py
    lst = [type(exc).__name__]
check("a counterpart whose feed is not reading normally is 'listing unconfirmed', not 'still listed'",
      len(lst) == 1 and lst[0].startswith("listing unconfirmed"))
check("the ledger line says nothing found is not proof", "nothing found (not proof)" in D.line(rep(base)))
r = rep(base + [seen("brevard:fl511:3", "2026-11-04T10:00:00Z", dict(FL, road="Minton Rd", where="Minton Rd at Norfolk Pkwy",
                                                                      status="flooded", fullClosure=False)),
                gone("brevard:fl511:3", "2026-11-04T12:00:00Z")])
check("all four agreed groups are always reported, even with no drops in them",
      list(r["trial"]["byGroup"])[:4] == D.GROUPS and r["trial"]["byGroup"]["flooding/hazard, local street"]["episodes"] == 1
      and r["trial"]["byGroup"]["flooding/hazard, state road"]["episodes"] == 1
      and r["trial"]["byGroup"]["crash, state road"]["episodes"] == 0 and "not proof" in r["trial"]["limits"])
check("Codex repro: W New Haven Ave (US-192) is a state road; Minton Rd is a local street; no road is unknown",
      D.group({"title": "Flooding", "road": "W New Haven Ave"}) == "flooding/hazard, state road"
      and D.group({"title": "MVC", "road": "Minton Rd"}) == "crash, local street"
      and D.group({"title": "Flooding", "road": "Malabar Rd", "where": "Malabar Rd at Minton Rd"}) == "flooding/hazard, road class unknown"
      and D.group({"title": "MVC", "road": "Minton Rd", "where": "Minton Rd at US-192"}) == "crash, local street"
      and D.group({"title": "MVC", "where": "Hibiscus Blvd at Malabar Rd"}) == "crash, local street"
      and D.group({"title": "MVC", "where": "US-1 at Suntree Blvd"}) == "crash, state road" and D.group({"title": "MVC"}) == "crash, road unknown")
D.AUDIT = os.path.join(tmp, "audit"); os.makedirs(D.AUDIT, exist_ok=True)
open(os.path.join(D.AUDIT, "drops.json"), "w").write("{not json")
real = E.EVENTS
D.main()
check("a corrupt drops.json does not stop the report from refreshing", json.load(open(os.path.join(D.AUDIT, "drops.json"))).get("fresh") is True)
real_report = D.report
D.report = lambda *a, **k: 1 / 0
D.main()
D.report = real_report
bad = json.load(open(os.path.join(D.AUDIT, "drops.json")))
check("a failed check keeps the last good report, marked not fresh with the failure", bad["fresh"] is False and bad["lastFailure"]
      and "cases" in bad)

print("failures:", fails)
sys.exit(1 if fails else 0)
