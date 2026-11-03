#!/usr/bin/env python3
"""reconcile.plan(): what a run adds to the closure history. A record is marked missing only when its source was read
successfully; a failed or suspiciously empty source changes nothing. Also: stable FHP ids and Eastern-time parsing.
  python3 -I tests/test_reconcile.py <repo>
"""
import ast
import datetime
import os
import subprocess
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import events as E  # noqa: E402
import reconcile as R  # noqa: E402

fails = 0
T0 = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)
tmp = tempfile.mkdtemp(prefix="reconcile-test-")


def check(label, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + label)
    fails += not ok


def item(i, layer="Closures", **kw):
    it = {"id": f"fl511-{i}", "layer": layer, "status": "closed", "fullClosure": True, "road": "SR-507",
          "where": f"SR-507 NB at Street {i}", "reported": "2026-11-02T11:00:00Z", "updated": "2026-11-02T11:30:00Z"}
    it.update(kw)
    return it


def run(path, obs, hours):
    now = T0 + datetime.timedelta(hours=hours)
    state = E.derive(E.load(path), now)
    new, notes = R.plan(obs, state, E.iso(now))
    return E.append(new, path, now=now), notes, E.derive(E.load(path), now)["records"]


p = os.path.join(tmp, "a.jsonl")
obs = {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3), item(4)]}
w, _, recs = run(p, obs, 0)
check("first run reports four records", sum(e["kind"] == "reported" for e in w) == 4 and len(recs) == 4)
w, _, _ = run(p, obs, 1)
check("an identical second run adds nothing", w == [])

w, _, recs = run(p, {"sources": {"fl511": "down"}, "items": []}, 2)
check("feed down: nothing marked missing, all active", not any(e["kind"] == "missing" for e in w)
      and all(r["status"] == "active" and not r["missingSince"] for r in recs.values()))
w, notes, recs = run(p, {"sources": {"fl511": "empty"}, "items": []}, 3)
check("feed empty while we hold 4 of its records: treated as a fault, nothing missing, a note shown",
      not any(e["kind"] == "missing" for e in w) and notes and all(not r["missingSince"] for r in recs.values()))
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2)]}, 4)
check("feed up without two records: those two marked missing, still active",
      sum(e["kind"] == "missing" for e in w) == 2
      and all(r["status"] == "active" for r in recs.values()) and recs["brevard:fl511:3"]["missingSince"])
w, _, _ = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2)]}, 5)
check("still missing next run: no repeated missing events", not any(e["kind"] == "missing" for e in w))
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3)]}, 6)
check("a record listed again clears missingSince", recs["brevard:fl511:3"]["missingSince"] is None)
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3, status="caution", fullClosure=False)]}, 7)
check("a downgraded listing keeps the record closed and flags it",
      recs["brevard:fl511:3"]["fields"]["status"] == "closed" and recs["brevard:fl511:3"]["recheck"])

p2 = os.path.join(tmp, "b.jsonl")
run(p2, {"sources": {"fl511": "up"}, "items": [item(1)]}, 0)
w, _, recs = run(p2, {"sources": {"fl511": "empty"}, "items": []}, 1)
check("a valid empty feed with fewer than 3 open records marks them missing (still active)",
      any(e["kind"] == "missing" for e in w) and recs["brevard:fl511:1"]["status"] == "active")

d = R.display(E.derive(E.load(p)))[0]
check("display keeps every active record in the page schema", len(d) == 4 and all("where" in it and it["id"] for it in d))

# FHP roadblock wording (the old parser read 'ROADBLOCK' inside 'NO ROADBLOCK' as a full closure)
cases = [("MVC", "No Roadblock", False), ("MVC", "No Road Block", False), ("MVC W/O ROADBLOCK", "", False),
         ("MVC WITHOUT ROADBLOCK", "", False), ("MVC", "Partial Roadblock - Use Caution", False),
         ("MVC", "Roadblock", True), ("MVC", "Road Blocked", True), ("MVC", "All Lanes Blocked", True),
         ("ROAD CLOSED", "", True), ("MVC", "Roadway Open", False), ("MVC", "Right lane blocked", False),
         ("MVC W/Roadblock", "Partial Roadblock - Use Caution", True), ("MVC W/O Roadblock", "No Roadblock", False)]
bad = [(t_, r_) for t_, r_, want in cases if R.fhp_full_closure(t_, r_) != want]
check(f"FHP full-closure wording: no/w-o/without/partial roadblock are not full closures ({bad})", not bad)
fix = R.fhp_normalize({"layer": "FHP", "title": "Mvc at BEACHLINE EXPY WB [MM52] (No Roadblock)", "lanes": "No Roadblock",
                       "fullClosure": True})
check("migration normalizes the old misreading with an audit note", fix and fix["now"] is False and "parser correction" in fix["reason"])
check("a correct FHP flag is left alone", R.fhp_normalize({"layer": "FHP", "title": "Mvc at X", "lanes": "Roadblock",
                                                          "fullClosure": True}) is None)
p3 = os.path.join(tmp, "c.jsonl")
fhp = lambda **kw: dict({"id": "fhp-1", "layer": "FHP", "status": "caution", "road": "SR-528", "spot": "at MM52",
                         "reported": "2026-11-02T11:00:00Z", "updated": "2026-11-02T11:00:00Z", "title": "Mvc at SR-528"}, **kw)
run(p3, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=True, lanes="Roadblock")]}, 0)
w, _, recs = run(p3, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=False, lanes="No Roadblock",
                                                              updated="2026-11-02T12:00:00Z")]}, 1)
r3 = next(iter(recs.values()))
check("a genuine earlier roadblock followed by 'No Roadblock' stays a full closure, flagged",
      r3["fields"]["fullClosure"] and r3["recheck"])
p4 = os.path.join(tmp, "d.jsonl")
run(p4, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=False, lanes="Roadway Open")]}, 0)
it4 = R.display(E.derive(E.load(p4)))[0][0]
check("a 'Roadway Open' remark on an active record reads as an unverified dated note",
      it4["lanes"].startswith('Source said "Roadway Open"') and "not a verified reopening" in it4["lanes"])

# FHP ids are the same in separate processes (the old id used Python's per-process hash)
code = ("import sys; sys.path.insert(0, sys.argv[1]); import reconcile as R; "
        "print(R.rid_of({'layer': 'FHP', 'id': 'fhp-x', 'reported': '2026-11-02T14:14:28Z', "
        "'road': 'W King St', 'spot': 'at Clearlake Rd'}))")
ids = {subprocess.run([sys.executable, "-c", code, repo], capture_output=True, text=True,
                      env={"PYTHONHASHSEED": str(n)}).stdout.strip() for n in (1, 2, 3)}
check("FHP record id is stable across processes", len(ids) == 1 and next(iter(ids)).startswith("brevard:fhp:2026"))

# Eastern time across daylight saving (the old code used a fixed EST offset)
src = open(os.path.join(repo, "update_flood_data.py")).read()
tree = ast.parse(src)
keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "TZ" for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name in ("iso", "parse_fhp_time", "parse_fl511_time"))]
ns = {}
exec(compile(ast.Module(body=keep, type_ignores=[]), "update_flood_data.py", "exec"), ns)
check("FHP time in summer (EDT) converts with -4 h", ns["parse_fhp_time"]("07/04/2026 08:00:00") == "2026-07-04T12:00:00Z")
check("FHP time in winter (EST) converts with -5 h", ns["parse_fhp_time"]("11/03/2026 08:00:00") == "2026-11-03T13:00:00Z")
check("FL511 time converts with daylight saving", ns["parse_fl511_time"]("7/4/26, 8:00 AM") == "2026-07-04T12:00:00Z")

print("failures:", fails)
sys.exit(1 if fails else 0)
