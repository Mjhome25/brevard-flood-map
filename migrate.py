#!/usr/bin/env python3
"""One-time bootstrap of closure_events.jsonl from every flood_data.json snapshot in git history up to a frozen
cutoff commit (Phase 1 batch 2). Run by hand, reviewed before its commit.

  python3 migrate.py --cutoff <commit>            dry run: prints the grouped report, writes nothing
  python3 migrate.py --cutoff <commit> --write    appends the events (refuses if the history already exists)

Every record ever published becomes a `reported` event with its first published version (original category,
fullClosure, source and times; never relabeled as confirmed), then a `relisted` with its last published version
(which the reducer can only raise, never lower). A record missing from the cutoff snapshot also gets `missing` and a
`recheck` saying it was recovered: its absence is not an all-clear. Nothing here can clear a record.
"""
import argparse
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events as E  # noqa: E402
from reconcile import fields_of, rid_of, src_of, stime_of  # noqa: E402

def git(*args) -> str:
    return subprocess.run(["git", "-C", E.HERE, *args], capture_output=True, text=True, check=True).stdout


def scan(cutoff: str):
    revs = [line.split() for line in git("log", "--reverse", "--format=%H %cI", cutoff, "--", "flood_data.json").splitlines()]
    first, last, seen_at = {}, {}, {}
    final_ids = set()
    for n, (h, t) in enumerate(revs):
        try:
            d = json.loads(git("show", f"{h}:flood_data.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        ids = set()
        for it in d.get("items", []):
            if it.get("status") not in E.SEVERITY:
                continue
            r = rid_of(it)
            ids.add(r)
            first.setdefault(r, (h, t, it))
            last[r] = (h, t, it)
            seen_at[r] = t
        if n == len(revs) - 1:
            final_ids = ids
    return revs, first, last, seen_at, final_ids


def build(cutoff: str):
    revs, first, last, seen_at, final_ids = scan(cutoff)
    cutoff_full = git("rev-parse", cutoff).strip()
    evs, groups = [], collections.defaultdict(list)
    for r in sorted(first):
        h0, t0, it0 = first[r]
        h1, t1, it1 = last[r]
        base = {"rid": r, "src": src_of(it0), "actor": "migration", "migratedFrom": cutoff_full[:12]}
        s0 = stime_of(it0, "")
        if not E.parse(s0):
            groups["unlocatable (no usable source time)"].append(r)
            continue
        evs.append({**base, "kind": "reported", "stime": s0, "fields": fields_of(it0)})
        s1 = stime_of(it1, s0)
        if fields_of(it1) != fields_of(it0) or s1 != s0:
            evs.append({**base, "kind": "relisted", "stime": s1, "fields": fields_of(it1)})
        if it1.get("lat") is None:
            groups["no map location (list only)"].append(r)
        if r in final_ids:
            groups["still published at cutoff"].append(r)
        else:
            evs.append({**base, "kind": "missing", "stime": s1, "lastSeen": seen_at[r]})
            evs.append({**base, "kind": "recheck", "stime": s1,
                        "reason": f"recovered from history: no longer listed after {seen_at[r][:16]}; no official reopening found"})
            groups["recovered (vanished before Phase 1, no clearance evidence)"].append(r)
    return revs, cutoff_full, evs, groups, last


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cutoff", required=True)
    p.add_argument("--write", action="store_true")
    p.add_argument("--events", default=E.EVENTS)
    a = p.parse_args(argv)
    revs, cutoff_full, evs, groups, last = build(a.cutoff)
    print(f"migration from {len(revs)} flood_data.json snapshots up to {cutoff_full[:12]}: {len(evs)} events")
    print("groups:")
    for g in ("still published at cutoff", "recovered (vanished before Phase 1, no clearance evidence)",
              "no map location (list only)", "unlocatable (no usable source time)"):
        print(f"  {g}: {len(groups.get(g, []))}")
    print("  evidenced clearance found: 0 (none of the history carries official reopening evidence)")
    for r in groups.get("recovered (vanished before Phase 1, no clearance evidence)", []):
        it = last[r][2]
        print(f"    {r}  {it.get('status')}{' full' if it.get('fullClosure') else ''}  "
              f"{(it.get('where') or it.get('title') or '')[:60]}")
    if not a.write:
        print("dry run: nothing written")
        return 0
    if E.load(a.events):
        sys.exit("migrate: the history already exists; migration runs once")
    written = E.append(evs, a.events)
    print(f"wrote {len(written)} events to {a.events}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
