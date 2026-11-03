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
CRASH = {"road": "US-1", "where": "US-1 at Viera Blvd", "title": "MVC", "status": "mvc", "fullClosure": False,
         "lat": 28.2, "lng": -80.7}


def ev(kind, rid, at, src="fhp", **kw):
    return dict(kind=kind, rid=rid, src=src, at=at, actor="test", **kw)


def rep(events, since="", water=None):
    p = os.path.join(tmp, "e.jsonl")
    open(p, "w").write("".join(json.dumps(e) + "\n" for e in events))
    w = os.path.join(tmp, "w.json")
    json.dump(water or {}, open(w, "w"))
    before = open(p).read()
    out = A.run(NOW, since, p, w)
    assert open(p).read() == before, "the audit wrote the closure history"
    return out


def drop(rid, at, fields=CRASH):
    return [ev("reported", rid, "2026-11-03T10:00:00Z", stime="2026-11-03T10:00:00Z", fields=fields),
            ev("missing", rid, "2026-11-03T11:00:00Z", stime="2026-11-03T11:00:00Z"),
            ev("cleared", rid, at, evidence={"kind": "fhp-dropped", "url": E.FHP_URL, "missingSince": "2026-11-03T11:00:00Z",
                                             "reopenedAt": at})]


base = drop("brevard:fhp:1", "2026-11-03T11:30:00Z")
r = rep(base)
check("a dropped FHP crash with nothing else at that place is 'unknown', never open",
      r["removed"] == 1 and r["rows"][0]["verdict"] == "unknown" and r["rows"][0]["where"] == "US-1 at Viera Blvd")
r = rep(base + [ev("relisted", "brevard:fhp:1", "2026-11-03T12:00:00Z", stime="2026-11-03T10:00:00Z", fields=CRASH)])
check("FHP listing the same crash again after the drop is a conflict", r["conflict"] == 1)
other = dict(CRASH, title="Closure", status="closed")
r = rep(base + [ev("reported", "brevard:fl511:9", "2026-11-03T12:00:00Z", src="fl511", stime="2026-11-03T12:00:00Z",
                   fields=other)])
check("another source reporting the same place after the drop is a conflict",
      r["conflict"] == 1 and "fl511" in r["rows"][0]["seen"][0])
r = rep([ev("reported", "brevard:fl511:9", "2026-11-03T09:00:00Z", src="fl511", stime="2026-11-03T09:00:00Z",
            fields=other)] + base)
check("another source's report at the same place still active at the drop is a conflict", r["conflict"] == 1)
far = dict(other, where="US-1 at Suntree Blvd")
r = rep(base + [ev("reported", "brevard:fl511:9", "2026-11-03T12:00:00Z", src="fl511", stime="2026-11-03T12:00:00Z",
                   fields=far)])
check("a report at a different cross street is not a conflict", r["conflict"] == 0)
closure = dict(CRASH, title="Road closure", status="closed")
r = rep(drop("brevard:fhp:2", "2026-11-03T11:30:00Z", closure))
check("a cleared event the history ignores (an FHP road closure) is not counted as a removal", r["removed"] == 0)
r = rep(base, since="2026-11-03T11:30:00Z")
check("removals already audited are not counted again", r["removed"] == 0)
off = [ev("reported", "brevard:manual:s", "2026-11-02T10:00:00Z", src="manual", stime="2026-11-02T10:00:00Z",
          fields=dict(CRASH, title="Flooding", status="flooded")),
       ev("cleared", "brevard:manual:s", "2026-11-03T14:00:00Z", src="manual",
          evidence={"kind": "official", "url": "https://www.melbourneflorida.org/x", "reopenedAt": "2026-11-03T13:30:00Z"})]
r = rep(off)
check("an official reopening with nothing contradicting it is corroborated-open",
      r["corroboratedOpen"] == 1 and r["rows"][0]["source"] == "https://www.melbourneflorida.org/x")
hist = [{"id": "melbourne-water-system", "from": "active", "to": "rescinded", "detectedAt": "2026-11-03T15:00:00Z",
         "sourceUrl": "https://www.melbourneflorida.org/r", "publishedDate": "2026-11-03"}]
r = rep([], water={"history": hist})
check("a boil-water rescission is audited", r["removed"] == 1 and r["rows"][0]["verdict"] == "corroborated-open")
r = rep([], water={"history": hist + [dict(hist[0], **{"from": "rescinded", "to": "active",
                                                          "detectedAt": "2026-11-03T15:30:00Z"})]})
check("a rescinded notice that comes back active is a conflict", r["conflict"] == 1)
r = rep([], water=["not", "a", "dict"])
check("an unreadable water file never stops the audit", r["removed"] == 0)

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
      and os.path.exists(os.path.join(A.AUDIT, "2026-11-03.json")))
E.utcnow = lambda: datetime.datetime(2026, 12, 4, 13, 0, tzinfo=datetime.timezone.utc)
A.main(["--due"])
check("stops after Dec 3", "2026-12-04" not in open(os.path.join(A.AUDIT, "ledger.md")).read())
E.utcnow = real_now

print("failures:", fails)
sys.exit(1 if fails else 0)
