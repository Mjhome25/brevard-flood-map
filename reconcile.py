#!/usr/bin/env python3
"""Turn this run's observations into closure history events, then write flood_data.json from the derived state.

  python3 reconcile.py      reads observations.json (from update_flood_data.py), county.json, closure_events.jsonl

A record seen this run is `reported` (new) or `relisted`; an active record its source no longer lists is `missing`
(a flag, never a status change). Source health is recorded on change. Clearance is not issued here: county clearance
is disabled until the verifier and the reopened display qualify (Phase 1 batches 3 and 4).
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import events as E  # noqa: E402

OBS = os.path.join(E.HERE, "observations.json")
OUT = os.path.join(E.HERE, "flood_data.json")
ANOMALY_MIN = 3  # an empty feed while we hold at least this many of its records is treated as a fault, not news


def rid_of(it: dict, county: str = "brevard") -> str:
    """Stable record ids. FHP: reported time + hash of road and cross street (the old id used Python's per-process
    hash, which changes between runs)."""
    layer, i = it.get("layer"), str(it.get("id", ""))
    if layer == "manual":
        return f"{county}:manual:" + i.removeprefix("man-")
    if layer == "FHP":
        key = hashlib.sha1(f"{it.get('road', '')}|{it.get('spot', '')}".encode()).hexdigest()[:8]
        return f"{county}:fhp:" + re.sub(r"\D", "", it.get("reported") or "") + "-" + key
    return f"{county}:fl511:" + i.removeprefix("fl511-")


def src_of(it: dict) -> str:
    return {"manual": "manual", "FHP": "fhp"}.get(it.get("layer"), "fl511")


def fields_of(it: dict) -> dict:
    return {k: it[k] for k in E.FIELDS if k in it and it[k] not in (None, "")}


def stime_of(it: dict, fallback: str = "") -> str:
    for k in ("updated", "reported"):
        if E.parse(it.get(k)):
            return it[k]
    return fallback


def plan(obs: dict, state: dict, now: str, county: str = "brevard") -> tuple[list[dict], list[str]]:
    """The events this run adds, and notes for the page's errors list."""
    new, notes = [], []
    for src, health in sorted(obs.get("sources", {}).items()):
        if state["sources"].get(src, {}).get("state") != health:
            new.append({"kind": "source", "src": src, "state": health, "actor": "reconcile", "stime": now})
    observed = set()
    for it in obs.get("items", []):
        if it.get("status") not in E.SEVERITY:
            continue
        rid = rid_of(it, county)
        observed.add(rid)
        kind = "relisted" if rid in state["records"] else "reported"
        new.append({"rid": rid, "kind": kind, "src": src_of(it), "actor": "reconcile", "stime": stime_of(it, now),
                    "fields": fields_of(it)})
    active_by_src = {}
    for r in state["records"].values():
        if r["status"] == "active":
            active_by_src.setdefault(r["src"], []).append(r)
    for src, recs in sorted(active_by_src.items()):
        health = obs.get("sources", {}).get(src)
        if health != "up" and not (health == "empty" and len(recs) < ANOMALY_MIN):
            if health == "empty":
                notes.append(f"{src}: returned no items while {len(recs)} of its reports are open; treated as a fault")
            continue  # down, unknown or anomalous empty: nothing is marked missing
        for r in recs:
            if r["rid"] not in observed and not r["missingSince"]:
                new.append({"rid": r["rid"], "kind": "missing", "src": src, "actor": "reconcile", "stime": now})
    return new, notes


def display(state: dict) -> tuple[list[dict], list[dict]]:
    """Active records in the page's existing item schema, plus reopened records still inside their green window."""
    items, reopened = [], []
    for r in state["records"].values():
        it = dict(r["fields"], id=r["rid"], updated=r["lastReported"], lastReported=r["lastReported"],
                  firstReported=r["firstReported"])
        if r["status"] == "active":
            it.update(awaitingOfficial=r["awaitingOfficial"], missingSince=r["missingSince"], recheck=r["recheck"])
            items.append(it)
        elif r["show"] == "green":
            it.update(reopenedAt=r["reopenedAt"], evidence=r["evidence"])
            reopened.append(it)
    items.sort(key=lambda it: it["id"])
    return items, reopened


def main() -> int:
    county = json.load(open(os.path.join(E.HERE, "county.json")))
    obs = json.load(open(OBS))
    now = E.utcnow()
    state = E.derive(E.load(), now)
    new, notes = plan(obs, state, E.iso(now), county["id"])
    written = E.append(new, clearance_enabled=bool(county.get("clearance", {}).get("enabled")), now=now)
    state = E.derive(E.load(), now)
    items, reopened = display(state)
    data = {"generated": obs["generated"], "errors": obs.get("errors", []) + notes, "items": items,
            "reopened": reopened, "sources": state["sources"], "alerts": obs.get("alerts", []),
            "advisories": obs.get("advisories", [])}
    json.dump(data, open(OUT, "w"), indent=1)
    print(f"reconcile: +{len(written)} events; {len(items)} active, {len(reopened)} reopened shown; notes={notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
