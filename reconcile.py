#!/usr/bin/env python3
"""Turn this run's observations into closure history events, then write flood_data.json from the derived state.

  python3 reconcile.py      reads observations.json (from update_flood_data.py), county.json, closure_events.jsonl

A record seen this run is `reported` (new) or `relisted`; an active record its source no longer lists is `missing`
(a flag, never a status change). Source health is recorded on change. Clearance is not issued here: county clearance
is disabled until the verifier and the reopened display qualify (Phase 1 batches 3 and 4).
"""
from __future__ import annotations

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


_FHP_NOT_FULL = re.compile(r"\b(?:NO|W/?O|WITHOUT|PARTIAL)\s+ROAD\s*BLOCK(?:ED)?", re.I)
_FHP_NEGATED = re.compile(r"\bNOT\b", re.I)
_FHP_TYPE_FULL = re.compile(r"^\s*ROAD(?:WAY)?\s+CLOSED\b", re.I)  # CAD types ROAD CLOSED, ROAD CLOSED DUE TO WATER
_FHP_FULL = re.compile(r"\bROAD(?:WAY)?\s+(?:IS\s+)?(?:CLOSED|BLOCKED)\b|\bALL\s+LANES\s+(?:ARE\s+)?(?:CLOSED|BLOCKED)\b",
                       re.I)
_FHP_PART = re.compile(r"\b(?:RIGHT|LEFT|CENTER|MIDDLE|INSIDE|OUTSIDE|ONE|TWO|THREE|SOME|\d+)\s+(?:\w+\s+)?LANES?\b"
                       r"|\bSHOULDER\b|\bRAMP\b", re.I)
_FHP_DIR = re.compile(r"\b(?:[NSEW]B|NORTH|SOUTH|EAST|WEST)(?:BOUND)?\b", re.I)
_FHP_BLOCK = re.compile(r"\bROAD\s*BLOCK(?:ED)?\b", re.I)
_FHP_OPEN = re.compile(r"\bOPEN\b|\bROA?I?D(?:WAY)?\s+CLEAR\b", re.I)


def fhp_classify(typ: str, remark: str) -> tuple[str, bool]:
    """FHP CAD row -> (status, fullClosure), the one classification the updater and the migration use.
    Only a closed-road vocabulary makes a full closure: the CAD type ROAD CLOSED (... DUE TO WATER), or a remark saying
    ROAD/ROADWAY CLOSED or BLOCKED or ALL LANES CLOSED/BLOCKED, with no NOT, no lane or shoulder scope, and no
    direction left open. A generic ROADBLOCK makes the row closed, not full. Any explicit negation (NOT, NO / W/O /
    WITHOUT / PARTIAL ROADBLOCK), lane scope ('Right lane closed') or 'open' wording keeps it a visible caution with
    the source's wording: ambiguity is shown as a hazard, never promoted to a closure."""
    remark = remark or ""
    text = f"{typ or ''} {remark}"
    negated = bool(_FHP_NOT_FULL.search(text) or _FHP_NEGATED.search(text))
    scoped = bool(_FHP_PART.search(remark) or (_FHP_DIR.search(remark) and _FHP_OPEN.search(remark)))
    said_full = bool(_FHP_FULL.search(remark)) and not (negated or scoped)
    full = bool(_FHP_TYPE_FULL.search(typ or "")) or said_full
    if re.search(r"flood|water", text, re.I):
        status = "flooded"
    elif full or (_FHP_BLOCK.search(_FHP_NOT_FULL.sub(" ", text)) and not (negated or scoped or _FHP_OPEN.search(text))):
        status = "closed"
    else:
        status = "caution"
    return status, full


def fhp_normalize(it: dict) -> dict | None:
    """For an FHP item recorded by the old parser (which read 'ROADBLOCK' inside 'NO ROADBLOCK' or 'PARTIAL
    ROADBLOCK' as a closure): the corrected status and fullClosure with an audit note, or None when nothing changes.
    The CAD type is the title before ' at ', and the remark is the lanes text."""
    if it.get("layer") != "FHP":
        return None
    typ = (it.get("title") or "").split(" at ")[0]
    status, full = fhp_classify(typ, it.get("lanes") or "")
    was = {"status": it.get("status"), "fullClosure": bool(it.get("fullClosure"))}
    now = {"status": status, "fullClosure": full}
    if was == now:
        return None
    return {"was": was, "now": now,
            "reason": f"parser correction: FHP type '{typ}' with remark '{it.get('lanes', '')}' reclassified"}


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


_OPEN_WORDS = re.compile(r"\b(?:ro\w*d(?:way)?\s+)?(?:open|clear(?:ed)?)\b", re.I)


def display(state: dict) -> tuple[list[dict], list[dict]]:
    """Active records in the page's existing item schema, plus reopened records still inside their green window."""
    items, reopened = [], []
    for r in state["records"].values():
        it = dict(r["fields"], id=r["rid"], updated=r["lastReported"], lastReported=r["lastReported"],
                  firstReported=r["firstReported"])
        if r["status"] == "active":
            it.update(awaitingOfficial=r["awaitingOfficial"], missingSince=r["missingSince"], recheck=r["recheck"])
            if it.get("lanes") and _OPEN_WORDS.search(it["lanes"]):
                # An old source remark such as "Roadway Open" is not a verified reopening; show it as a dated note.
                it["lanes"] = f"Source said \"{it['lanes']}\" ({r['firstReported'][:16].replace('T', ' ')} UTC); not a verified reopening"
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
