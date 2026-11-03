#!/usr/bin/env python3
"""Closure history: the only writer of closure_events.jsonl and the reducer that derives road state from it.

Mike's rule (November 3, 2026): a road or hazard never fades. It leaves the map only through a `cleared` event backed
by a cited official statement on a vetted government website. Nothing else (time, a feed dropping it, traffic speed,
an unofficial report) changes its status.

Each line of closure_events.jsonl is one event: {seq, at, eid, rid, kind, actor, src, stime, ...}.
  rid    record id, "<county>:<source>:<key>"; one source report per record, records are never merged
  src    source key (fl511, fhp, manual, nws ...); stime is the source's own time for this observation
Kinds:
  reported   a record first appears (carries `fields`); reactivates a cleared record only if newer than its reopening
  relisted   the source still lists it; may raise severity or scope, never lower it (a lower reading flags recheck)
  missing    the source no longer lists it: flag only
  recheck    a reason to look again: flag only
  source     per-source health (`state`: up, down, empty): no record changes
  cleared    official reopening; written only by the verifier path (batch 3) while county clearance is enabled
Status comes only from reported and cleared.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "closure_events.jsonl")
KINDS = ("reported", "relisted", "missing", "recheck", "source", "cleared")
GREEN_HOURS = 24
RECHECK_AFTER_HOURS = 72
# Display fields a record carries. Geometry and location (where, lat, lng, approx, line) are set by the first report.
FIELDS = ("road", "roadShort", "dir", "spot", "where", "title", "city", "status", "fullClosure", "lanes", "lat", "lng",
          "approx", "source", "sourceUrl", "reported", "note", "layer", "line")
SCOPE_FIXED = ("road", "roadShort", "dir", "spot", "where", "city", "lat", "lng", "approx", "line", "layer", "reported")
SEVERITY = {"caution": 0, "hazard": 1, "mvc": 1, "flooded": 1, "closed": 2}


class Refused(ValueError):
    """An event that the history must not accept."""


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s) -> datetime.datetime | None:
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def eid_of(ev: dict) -> str:
    """Same observation, same id: rid, kind, source, source time and meaningful content; never fetch time."""
    core = {k: ev.get(k) for k in ("rid", "kind", "src", "stime", "fields", "state", "reason", "evidence")}
    return hashlib.sha256(canon(core).encode()).hexdigest()[:32]


def rank(f: dict) -> tuple:
    return (SEVERITY.get(f.get("status"), 1), bool(f.get("fullClosure")))


# ---- reading ---------------------------------------------------------------------------------------------------

def load(path: str = EVENTS) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                raise Refused(f"line {n}: blank line")
            out.append(json.loads(line))
    return out


def validate(ev: dict, prev_seq: int, clearance_enabled: bool) -> None:
    """Schema checks shared by append (issuance) and check_events (replay)."""
    if ev.get("kind") not in KINDS:
        raise Refused(f"unknown kind {ev.get('kind')!r}")
    if ev.get("seq") != prev_seq + 1:
        raise Refused(f"seq {ev.get('seq')} after {prev_seq}")
    if not parse(ev.get("at")):
        raise Refused(f"seq {ev.get('seq')}: bad at")
    if ev.get("eid") != eid_of(ev):
        raise Refused(f"seq {ev.get('seq')}: eid does not match content")
    kind = ev["kind"]
    if kind != "source" and not str(ev.get("rid", "")).count(":") >= 2:
        raise Refused(f"seq {ev['seq']}: bad rid {ev.get('rid')!r}")
    if kind in ("reported", "relisted"):
        f = ev.get("fields")
        if not isinstance(f, dict) or f.get("status") not in SEVERITY:
            raise Refused(f"seq {ev['seq']}: {kind} needs fields with a known status")
        if not parse(ev.get("stime")):
            raise Refused(f"seq {ev['seq']}: {kind} needs a source time")
    if kind == "source" and ev.get("state") not in ("up", "down", "empty"):
        raise Refused(f"seq {ev['seq']}: bad source state")
    if kind == "recheck" and not ev.get("reason"):
        raise Refused(f"seq {ev['seq']}: recheck needs a reason")
    if kind == "cleared":
        if not clearance_enabled:
            raise Refused(f"seq {ev['seq']}: clearance is disabled for this county")
        raise Refused(f"seq {ev['seq']}: no clearance verifier is installed yet")  # batch 3 replaces this


# ---- writing (the only writer) ---------------------------------------------------------------------------------

def append(new: list[dict], path: str = EVENTS, clearance_enabled: bool = False, now: datetime.datetime | None = None
           ) -> list[dict]:
    """Append events that are not already in the history. Returns the events actually written."""
    history = load(path)
    seen = {e["eid"] for e in history}
    seq = history[-1]["seq"] if history else 0
    at = iso(now or utcnow())
    written = []
    for raw in new:
        if raw.get("kind") == "cleared":
            raise Refused("cleared events come only from the verifier path")
        ev = {k: v for k, v in raw.items() if k not in ("seq", "at", "eid")}
        ev["eid"] = eid_of(ev)
        if ev["eid"] in seen:
            continue
        ev["seq"], ev["at"] = seq + 1, at
        ev = {k: ev[k] for k in ("seq", "at", "eid", *sorted(k for k in ev if k not in ("seq", "at", "eid")))}
        validate(ev, seq, clearance_enabled)
        seq += 1
        seen.add(ev["eid"])
        written.append(ev)
    if written:
        with open(path, "a", encoding="utf-8") as fh:
            for ev in written:
                fh.write(canon(ev) + "\n")
    return written


# ---- reducing --------------------------------------------------------------------------------------------------

def _merge_up(cur: dict, new: dict) -> tuple[dict, bool]:
    """Raise severity or scope only. Returns (merged fields, whether the source showed a lower impact)."""
    merged = dict(cur)
    lower = rank(new) < rank(cur)
    if rank(new) > rank(cur):
        merged["status"] = new.get("status", cur.get("status"))
        merged["fullClosure"] = bool(cur.get("fullClosure")) or bool(new.get("fullClosure"))
        if new.get("lanes"):
            merged["lanes"] = new["lanes"]  # only with a strictly higher severity
    elif new.get("fullClosure") and not cur.get("fullClosure"):
        merged["fullClosure"] = True
    if not cur.get("lanes") and new.get("lanes") and not lower:
        merged["lanes"] = new["lanes"]
    for k in ("title", "note", "source", "sourceUrl"):  # descriptive text; scope fields never change here
        if new.get(k) and not lower:
            merged[k] = new[k]
    return merged, lower


def derive(events: list[dict], now: datetime.datetime | None = None) -> dict:
    """Road state at `now`: {"records": {rid: state}, "sources": {src: health}}."""
    now = now or utcnow()
    recs: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for ev in events:
        kind, rid = ev["kind"], ev.get("rid")
        if kind == "source":
            sources[ev["src"]] = {"state": ev["state"], "since": ev["at"]}
            continue
        r = recs.get(rid)
        st = parse(ev.get("stime"))
        if kind in ("reported", "relisted"):
            if r is None:
                recs[rid] = {"rid": rid, "src": ev.get("src"), "status": "active", "fields": dict(ev["fields"]),
                             "firstReported": ev["stime"], "lastReported": ev["stime"], "recheck": [],
                             "missingSince": None}
                continue
            if r["status"] == "reopened":
                if st and st > parse(r["reopenedAt"]):
                    r.update(status="active", fields=dict(ev["fields"]), lastReported=ev["stime"], recheck=[],
                             missingSince=None, reopenedAt=None, evidence=None)
                continue
            r["fields"], lower = _merge_up(r["fields"], ev["fields"])
            if st and st > parse(r["lastReported"]):
                r["lastReported"] = ev["stime"]
            r["missingSince"] = None
            if lower and "source shows reduced impact" not in r["recheck"]:
                r["recheck"].append("source shows reduced impact")
        elif r is None:
            continue
        elif kind == "missing":
            if r["status"] == "active" and not r["missingSince"]:
                r["missingSince"] = ev["at"]
        elif kind == "recheck":
            if r["status"] == "active" and ev["reason"] not in r["recheck"]:
                r["recheck"].append(ev["reason"])
        elif kind == "cleared":
            r.update(status="reopened", reopenedAt=ev["evidence"]["reopenedAt"], evidence=ev["evidence"])
    for r in recs.values():
        if r["status"] == "reopened":
            r["show"] = "green" if now < parse(r["reopenedAt"]) + datetime.timedelta(hours=GREEN_HOURS) else "hidden"
        else:
            r["show"] = "active"
            old = now - parse(r["lastReported"]) > datetime.timedelta(hours=RECHECK_AFTER_HOURS)
            r["awaitingOfficial"] = bool(r["missingSince"] or r["recheck"] or old)
    return {"records": recs, "sources": sources}
