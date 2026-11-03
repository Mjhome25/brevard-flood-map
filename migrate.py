#!/usr/bin/env python3
"""One-time bootstrap of closure_events.jsonl from every flood_data.json snapshot in git history up to a frozen
cutoff commit (Phase 1 batch 2). Run by hand, reviewed before its commit.

  python3 migrate.py --cutoff <commit>            dry run: prints the grouped report, writes nothing
  python3 migrate.py --cutoff <commit> --write    appends the events (refuses if the history already exists)

Every snapshot is replayed in order: a record's first published version is `reported` (original category,
fullClosure, source and times; never relabeled as confirmed) and each later changed version is `relisted`, which the
reducer can only raise, never lower, so a stronger restriction seen in any snapshot is kept. A record missing from the cutoff snapshot also gets `missing` and a
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


def snapshots(cutoff: str):
    """Chronological flood_data.json snapshots up to the cutoff: [(commit, time, items)]."""
    out = []
    for h, t in (line.split() for line in git("log", "--reverse", "--format=%H %cI", cutoff, "--", "flood_data.json").splitlines()):
        try:
            d = json.loads(git("show", f"{h}:flood_data.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        out.append((h, t, [it for it in d.get("items", []) if it.get("status") in E.SEVERITY]))
    return out


def manual_only(cutoff: str, known: set) -> list[str]:
    """Hand-entered ids that appear in manual_closures.json history but never in a published snapshot."""
    ids = set()
    for h in git("log", "--format=%H", cutoff, "--", "manual_closures.json").split():
        try:
            ids |= {rid_of(dict(c, layer="manual")) for c in json.loads(git("show", f"{h}:manual_closures.json"))}
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
    return sorted(ids - known)


def build(cutoff: str):
    """Replay every snapshot in order: each record's first observation is `reported`, and every later observation
    whose content changed is `relisted`, so an intermediate stronger restriction is kept (the reducer only raises)."""
    snaps = snapshots(cutoff)
    cutoff_full = git("rev-parse", cutoff).strip()
    evs, groups, prev, last, seen_at = [], collections.defaultdict(list), {}, {}, {}
    for h, t, items in snaps:
        for it in items:
            r = rid_of(it)
            f, st = fields_of(it), stime_of(it, "")
            if not E.parse(st):
                groups["unlocatable (no usable source time)"].append(r)
                continue
            if prev.get(r) != (f, st):
                evs.append({"rid": r, "src": src_of(it), "actor": "migration", "migratedFrom": cutoff_full[:12],
                            "kind": "relisted" if r in prev else "reported", "stime": st, "fields": f, "snapshot": h[:12]})
                prev[r] = (f, st)
            last[r], seen_at[r] = it, t
    final_ids = {rid_of(it) for it in snaps[-1][2]} if snaps else set()
    for r in sorted(last):
        if last[r].get("lat") is None:
            groups["no map location (list only)"].append(r)
        if r in final_ids:
            groups["still published at cutoff"].append(r)
            continue
        base = {"rid": r, "src": src_of(last[r]), "actor": "migration", "migratedFrom": cutoff_full[:12],
                "stime": stime_of(last[r], "")}
        evs.append({**base, "kind": "missing", "lastSeen": seen_at[r]})
        evs.append({**base, "kind": "recheck",
                    "reason": f"recovered from history: no longer listed after {seen_at[r][:16]}; no official reopening found"})
        groups["recovered (vanished before Phase 1, no clearance evidence)"].append(r)
    groups["hand-entered only in manual_closures.json, never published (not imported)"] = manual_only(cutoff, set(last))
    groups["unlocatable (no usable source time)"] = sorted(set(groups["unlocatable (no usable source time)"]))
    return snaps, cutoff_full, evs, groups, last


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cutoff", required=True)
    p.add_argument("--write", action="store_true")
    p.add_argument("--events", default=E.EVENTS)
    a = p.parse_args(argv)
    snaps, cutoff_full, evs, groups, last = build(a.cutoff)
    print(f"migration from {len(snaps)} flood_data.json snapshots up to {cutoff_full[:12]}: {len(evs)} candidate events")
    print("groups:")
    for g in ("still published at cutoff", "recovered (vanished before Phase 1, no clearance evidence)",
              "no map location (list only)", "unlocatable (no usable source time)",
              "hand-entered only in manual_closures.json, never published (not imported)"):
        print(f"  {g}: {len(groups.get(g, []))}")
    print("  evidenced clearance found: 0 (none of the history carries official reopening evidence)")
    for r in groups.get("recovered (vanished before Phase 1, no clearance evidence)", []):
        it = last[r]
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
