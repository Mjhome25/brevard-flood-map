#!/usr/bin/env python3
"""The daily removal audit: every report that left the map gets a verdict; it never writes the closure history."""
import datetime
import json
import os
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import audit as A  # noqa: E402
import events as E  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok  " if ok else "FAIL"), name)
    fails += not ok


tmp = tempfile.mkdtemp()
NOW = datetime.datetime(2026, 11, 3, 16, 0, tzinfo=datetime.timezone.utc)
CRASH = {"sourceUrl": E.FHP_URL, "road": "US-1", "where": "US-1 at Viera Blvd", "title": "MVC", "status": "mvc", "fullClosure": False,
         "lat": 28.2, "lng": -80.7}


def ev(kind, rid, at, src="fhp", **kw):
    return dict(kind=kind, rid=rid, src=src, at=at, actor="test", **kw)


def rep(events, water=None, known=None, now=NOW):
    p = os.path.join(tmp, "e.jsonl")
    open(p, "w").write("".join(json.dumps(dict(e, eid=f"e{i}")) + "\n" for i, e in enumerate(events)))
    w = os.path.join(tmp, "w.json")
    if water is None:
        if os.path.exists(w):
            os.remove(w)
    else:
        json.dump(water, open(w, "w"))
    before = open(p).read()
    out = A.run(now, p, w, known)
    assert open(p).read() == before, "the audit wrote the closure history"
    return out


def drop(rid, at, fields=CRASH):
    return [ev("reported", rid, "2026-11-03T10:00:00Z", stime="2026-11-03T10:00:00Z", fields=fields),
            ev("missing", rid, "2026-11-03T11:00:00Z", stime="2026-11-03T11:00:00Z"),
            ev("cleared", rid, at, evidence={"kind": "fhp-dropped", "url": E.FHP_URL, "missingSince": "2026-11-03T11:00:00Z",
                                             "reopenedAt": at})]


V = lambda r, kind="removed": [x["verdict"] for x in r["rows"] if x["kind"] == kind]
base = drop("brevard:fhp:1", "2026-11-03T11:30:00Z")
r = rep(base, water={})
check("a dropped FHP crash with nothing else at that place is 'unknown', never open (and its disappearance is a case)",
      V(r) == ["unknown"] and V(r, "disappeared") == ["unknown"] and r["rows"][0]["where"] == "US-1 at Viera Blvd")
r = rep(base + [ev("relisted", "brevard:fhp:1", "2026-11-03T12:00:00Z", stime="2026-11-03T10:00:00Z", fields=CRASH)])
check("FHP listing the same incident again after dropping it is source-relisted", V(r) == ["source-relisted"])
other = dict(CRASH, title="Closure", status="closed")
early = ev("reported", "brevard:fl511:9", "2026-11-03T09:00:00Z", src="fl511", stime="2026-11-03T09:00:00Z", fields=other)
again = ev("relisted", "brevard:fl511:9", "2026-11-03T12:00:00Z", src="fl511", stime="2026-11-03T12:00:00Z", fields=other)
check("another source's closure that began before the drop and was reported again after it is still-blocked",
      V(rep([early] + base + [again])) == ["still-blocked"])
check("another source's closure active at the drop but not reported since is stale-unresolved",
      V(rep([early] + base)) == ["stale-unresolved"])
late = ev("reported", "brevard:fl511:9", "2026-11-03T15:00:00Z", src="fl511", stime="2026-11-03T15:00:00Z", fields=other)
check("a report that began after the drop is a later-report (a recurrence or proof), not proof", V(rep(base + [late])) == ["later-report"])
weak = dict(late, fields=dict(other, status="caution", title="Shoulder blocked"))
check("a weaker impact (shoulder caution) at the same place is not evidence against a crash removal", V(rep(base + [weak])) == ["unknown"])
far = dict(late, fields=dict(other, where="US-1 at Suntree Blvd"))
check("a report at a different cross street is not evidence", V(rep(base + [far])) == ["unknown"])
closure = dict(CRASH, title="Road closure", status="closed")
r = rep(drop("brevard:fhp:2", "2026-11-03T11:30:00Z", closure))
check("an FHP road closure that disappeared but stayed on the map is a 'disappeared' case, not a removal",
      V(r) == [] and V(r, "disappeared") == ["unknown"] and r["rows"][0]["status"] == "closed"
      and r["rows"][0]["source"] == E.FHP_URL)
d1 = rep(base, water={})
known = {k: v for k, v in d1["book"].items()}
d2 = rep(base + [late], water={}, known=known, now=NOW + datetime.timedelta(days=1))
row = [x for x in d2["rows"] if x["kind"] == "removed"][0]
check("day 1 unknown -> day 2 contradicting evidence: the old case is re-judged, marked changed, history kept",
      row["verdict"] == "later-report" and row["changed"] and not row["new"] and row["firstChecked"] == "2026-11-03"
      and [h["verdict"] for h in d2["book"][row["id"]]["verdicts"]] == ["unknown", "later-report"])
off = [ev("reported", "brevard:manual:s", "2026-11-02T10:00:00Z", src="manual", stime="2026-11-02T10:00:00Z",
          fields=dict(CRASH, road="Sarno Rd", where="Sarno Rd at Eau Gallie Blvd", title="Flooding", status="flooded")),
       ev("cleared", "brevard:manual:s", "2026-11-03T14:00:00Z", src="manual",
          evidence={"kind": "official", "url": "https://www.melbourneflorida.org/x", "quote": "Sarno Road is open.",
                    "publishedDate": "2026-11-03", "sourceTime": "2026-11-03T13:30:00Z",
                    "reopenedAt": "2026-11-03T13:30:00Z"})]
r = rep(off)
check("an official reopening with nothing contradicting it is corroborated-open",
      V(r) == ["corroborated-open"] and r["rows"][0]["source"] == "https://www.melbourneflorida.org/x")
hist = [{"id": "melbourne-water-system", "from": "active", "to": "rescinded", "detectedAt": "2026-11-03T15:00:00Z",
         "sourceUrl": "https://www.melbourneflorida.org/r", "publishedDate": "2026-11-03"}]
r = rep([], water={"history": hist})
check("a boil-water rescission is audited", V(r, "water") == ["corroborated-open"] and r["complete"])
r = rep([], water={"history": hist + [dict(hist[0], **{"from": "rescinded", "to": "active",
                                                          "detectedAt": "2026-11-03T15:30:00Z"})]})
check("a rescinded notice that comes back active is flagged", V(r, "water") == ["source-relisted"])
for bad, why in ((None, "missing"), (["not", "a", "dict"], "corrupt")):
    r = rep(base, water=bad)
    check(f"a {why} water file is reported unavailable, never as zero rescissions",
          not r["complete"] and r["unavailable"] == ["water_notices.json"] and V(r) == ["unknown"])

t = rep([early] + base + [again])["dropTrial"]
check("the drop trial lists an FHP drop contradicted by a closure reported again after it, with the evidence",
      t["contradicted"] == ["brevard:fhp:1"] and t["forMike"][0]["seen"] and t["drops"] == 1)
t = rep([early] + base)["dropTrial"]
check("an older entry from another source still on the map is listed as stale, not as a contradiction",
      t["contradicted"] == [] and t["stale"] == ["brevard:fhp:1"])
check("a drop with nothing else at that place is counted but not listed", rep(base)["dropTrial"]["forMike"] == []
      and rep(base)["dropTrial"]["drops"] == 1)

# --due runs once a day, from 7 AM Eastern, until Dec 3; it writes only under audit/
A.AUDIT = os.path.join(tmp, "audit")
real_now = E.utcnow
E.utcnow = lambda: datetime.datetime(2026, 11, 3, 11, 0, tzinfo=datetime.timezone.utc)
A.main(["--due"])
check("not due before 12:00 UTC", not os.path.exists(os.path.join(A.AUDIT, "ledger.md")))
E.utcnow = lambda: datetime.datetime(2026, 11, 3, 12, 5, tzinfo=datetime.timezone.utc)
A.main(["--due"]); A.main(["--due"])
led = open(os.path.join(A.AUDIT, "ledger.md")).read()
check("due once at 12:00 UTC; a second run that day writes nothing", led.count("- 2026-11-03:") == 1
      and "FHP/FL511 drop trial (2026-11-03 to 2026-11-10)" in led
      and os.path.exists(os.path.join(A.AUDIT, "2026-11-03.json")) and os.path.exists(os.path.join(A.AUDIT, "cases.json")))
E.utcnow = lambda: datetime.datetime(2026, 11, 4, 12, 5, tzinfo=datetime.timezone.utc)
real_run = A.run
A.run = lambda *a, **k: 1 / 0
A.main(["--due"]); A.main(["--due"])
A.run = real_run
led = open(os.path.join(A.AUDIT, "ledger.md")).read()
check("a failed audit leaves one durable failure line a day and does not mark the day done",
      led.count("2026-11-04: AUDIT FAILED") == 1 and json.load(open(os.path.join(A.AUDIT, "last.json")))["date"] == "2026-11-03")
A.main(["--due"])
check("the next run after a failure retries and writes the day's report",
      os.path.exists(os.path.join(A.AUDIT, "2026-11-04.json")))
E.utcnow = lambda: datetime.datetime(2026, 12, 4, 13, 0, tzinfo=datetime.timezone.utc)
A.main(["--due"])
check("stops after Dec 3", "2026-12-04" not in open(os.path.join(A.AUDIT, "ledger.md")).read())
E.utcnow = real_now

print("failures:", fails)
sys.exit(1 if fails else 0)
