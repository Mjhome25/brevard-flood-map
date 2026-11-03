#!/usr/bin/env python3
"""Local check of migrate.py against the real git history (CI checkouts are shallow, so this is run by hand).
  python3 -I tests/check_migration.py <repo> <cutoff>
Asserts: every record ever published is in the migrated state and active; no record's category or fullClosure is
weaker than the strongest it ever showed; every named closed stretch (from/to/line) at the cutoff survives.
"""
import json, os, subprocess, sys, tempfile
repo, cutoff = os.path.abspath(sys.argv[1]), sys.argv[2]
sys.path.insert(0, repo)
import events as E, migrate as M  # noqa: E401,E402
from reconcile import rid_of  # noqa: E402
snaps, full, evs, groups, last = M.build(cutoff)
p = os.path.join(tempfile.mkdtemp(), "ev.jsonl")
E.append(evs, p)
recs = E.derive(E.load(p))["records"]
fails = 0
def check(label, ok):
    global fails; print(("ok   " if ok else "FAIL ") + label); fails += not ok
strongest = {}
for _, _, items in snaps:
    for it in items:
        r = rid_of(it); strongest[r] = max(strongest.get(r, (-1, False)), E.rank(it))
check(f"all {len(strongest)} published records migrated and active",
      set(strongest) == set(recs) and all(x["status"] == "active" for x in recs.values()))
weaker = [r for r, s in strongest.items() if E.rank(recs[r]["fields"]) < s]
check(f"no record weaker than its strongest published restriction ({weaker[:3]})", not weaker)
check("brevard:fl511:938170 keeps its closed full closure",
      recs.get("brevard:fl511:938170", {}).get("fields", {}).get("status") == "closed"
      and recs["brevard:fl511:938170"]["fields"].get("fullClosure"))
final = {rid_of(it): it for it in snaps[-1][2]}
named = {r: it for r, it in final.items() if it.get("from") or it.get("to")}
lost = [r for r, it in named.items() if any(recs[r]["fields"].get(k) != it.get(k) for k in ("from", "to", "line"))]
check(f"all {len(named)} named closed stretches at the cutoff survive unchanged ({lost[:3]})", named and not lost)
print("groups:", {k: len(v) for k, v in groups.items()})
print("failures:", fails); sys.exit(1 if fails else 0)
