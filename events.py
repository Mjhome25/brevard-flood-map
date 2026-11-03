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
  relisted   the source still lists it; may raise severity or scope, never lower it (a lower reading flags recheck).
             With `raisedBy` (host, url, agency, quote) it is a government page's own closure statement (Mike, Nov 3:
             "go by what the government page says and cite it"); it can only raise
  missing    the source no longer lists it: flag only
  recheck    a reason to look again: flag only
  source     per-source health (`state`: up, down, empty): no record changes
  verified   a government page confirms the report (`evidence`: host, url, agency, quote, publishedAt): a badge
             only, never a status change; the newest statement is shown
  cleared    official reopening; written only by the verifier path (batch 3) while county clearance is enabled
Status comes only from reported and cleared.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = os.path.join(HERE, "closure_events.jsonl")
KINDS = ("reported", "relisted", "missing", "recheck", "source", "verified", "cleared")
GREEN_HOURS = 24
RECHECK_AFTER_HOURS = 72
# Display fields a record carries. Geometry and location (where, lat, lng, approx, line) are set by the first report.
FIELDS = ("road", "roadShort", "dir", "spot", "where", "title", "city", "status", "fullClosure", "lanes", "lat", "lng",
          "approx", "source", "sourceUrl", "reported", "note", "layer", "line", "from", "to", "length_m")
# The closed stretch travels as one set: filled once when the record had none, never moved or narrowed afterwards.
EXTENT = ("line", "from", "to", "length_m")
SEVERITY = {"caution": 0, "hazard": 1, "mvc": 1, "flooded": 1, "closed": 2}


# Privacy (Mike, Nov 2): no fatality, injury or personal detail reaches the page from any source.
CRASH = re.compile(r"\b(?:(?:fatal(?:ity)?|serious|injury|traffic|vehicle|motor vehicle|multi-vehicle|vehicle fire\W+)?\s*(?:crash(?:es)?|collisions?|accidents?|wrecks?)|hit\s*(?:and|&)\s*run|fatal(?:ity|ities)?|pedestrian\s+(?:struck|crash|accident))\b", re.I)
SENSITIVE = re.compile(r"\b(?:with\s+)?(?:fatal(?:ity|ities)?|(?<!or-)deaths?|dead|died|dies|killed|deceased|injur(?:y|ies|ed)|entrap\w*|trapped|ejected|victims?|bod(?:y|ies)|trauma(?:\s+alert)?|airlift\w*|life\s*flight|transported|hospitali[sz]ed|critical(?:ly)?\s+(?:condition|injured)|drivers?|passengers?|occupants?|pedestrians?|juveniles?|man|woman|child(?:ren)?|teen(?:ager)?s?|person|people|(?:\d+|one|two|three)\s+(?:people|persons|vehicles?\s+occupants))\b", re.I)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VERIFIERS = ("verify.py/3",)
_R = r"(?P<road>[A-Z][A-Za-z0-9.' ]{1,40}?)"
_X = r"(?P<cross>[A-Z][A-Za-z0-9.' ]{1,40}?)"
# The only sentence shapes accepted as a government statement about one road. The whole sentence must match:
# anything else (other subjects, extra clauses, names, hedges) is unsupported and stays unverified.
NOTICE = [(re.compile(rf"^All of {_R} is (?P<impact>closed|flooded)\.?$"), True),
          (re.compile(rf"^{_R} at {_X} is (?P<impact>closed|flooded)\.?$"), False),
          (re.compile(rf"^(?P<impact>Flooding) on {_R} at {_X}\.?$"), False)]
SUFFIX = {"st": "street", "rd": "road", "ave": "avenue", "blvd": "boulevard", "dr": "drive", "pkwy": "parkway",
          "ln": "lane", "hwy": "highway", "ct": "court", "pl": "place", "cswy": "causeway"}


def road_words(s: str) -> str:
    """Lower-case road wording with suffixes spelled out: 'Babcock St' -> 'babcock street'."""
    return " ".join(SUFFIX.get(w, w) for w in re.findall(r"[a-z0-9]+", (s or "").lower()))


def notice(sentence: str) -> dict | None:
    """Parse one supported government sentence: {road, cross, whole, impact} or None."""
    for rx, whole in NOTICE:
        m = rx.match(sentence.strip())
        if m:
            g = m.groupdict()
            return {"road": road_words(g["road"]), "cross": road_words(g.get("cross") or ""), "whole": whole,
                    "impact": "flooded" if g["impact"].lower() in ("flooded", "flooding") else "closed"}
    return None
SHA = re.compile(r"^[0-9a-f]{64}$")


def verify_hosts(county_path: str = os.path.join(HERE, "county.json")) -> dict:
    """Exact government hosts whose pages may mark a report Government verified: {host: agency}."""
    try:
        return json.load(open(county_path)).get("verify", {}).get("hosts", {})
    except (OSError, ValueError):
        return {}


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


def eid_of(ev: dict, prev: str = "") -> str:
    """Hash of the event's content chained to the previous event's eid: unique and tamper-evident. Retries are not
    caught by the eid but by append(), which writes nothing that would not change the derived state."""
    core = {k: ev.get(k) for k in ("rid", "kind", "src", "stime", "fields", "state", "reason", "evidence")}
    if "raisedBy" in ev:  # added later; only hashed where present so earlier eids are unchanged
        core["raisedBy"] = ev["raisedBy"]
    return hashlib.sha256((prev + canon(core)).encode()).hexdigest()[:32]


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


GOV_FIELDS = ("host", "url", "finalUrl", "agency", "quote", "publishedDate", "checkedAt", "pageSha256", "verifier")


def supports(fields: dict, quote: str, raise_to: str | None = None) -> str | None:
    """None when the parsed sentence is about THIS report's road and supports its impact, else the reason it is not.
    The road is the sentence's subject; a cross street must be this report's unless the sentence covers all of the
    road; a closure sentence supports a closure (or raises to one), a flooding sentence only a flood report."""
    n = notice(quote)
    if not n:
        return "the sentence is not one of the supported road-notice forms"
    road = road_words(re.split(r"\s*\(|/", fields.get("roadShort") or fields.get("road") or "")[0])
    if not road or n["road"] != road:
        return f"the sentence is about {n['road']!r}, not this road ({road or 'unknown'})"
    spot = fields.get("spot") or (fields.get("where") or "").partition(" at ")[2]
    cross = road_words(re.sub(r"^(?:at|near|from)\s+", "", spot, flags=re.I).split(" to ")[0])
    if not n["whole"] and (not cross or n["cross"] != cross):
        return f"the sentence is about {n['cross']!r}, not this report's cross street ({cross or 'none recorded'})"
    if raise_to == "full" and not n["whole"]:
        return "a full closure needs a sentence covering all of the road"
    status = "closed" if raise_to else fields.get("status")
    if n["impact"] == "closed" and status != "closed":
        return "the sentence says closed but the report is not a closure (use --closed to raise it)"
    if n["impact"] == "flooded" and (raise_to or status != "flooded"):
        return "a flooding sentence supports a flood report only, never a closure"
    return None


def _check_gov(seq, v: dict, at: str) -> None:
    """Evidence read from a government page by verify.py: every receipt field present and well formed, https on an exact
    host from the county's verify list (also after redirects), the configured agency for that host, a real page date
    not after the event, a check time not after the event, a supported verifier, and a quote that is exactly one
    supported road sentence (so it can carry nothing but road wording)."""
    if not isinstance(v, dict) or not all(v.get(k) for k in GOV_FIELDS):
        raise Refused(f"seq {seq}: government evidence needs {', '.join(GOV_FIELDS)}")
    hosts = verify_hosts()
    for u in (v["url"], v["finalUrl"]):
        p = urllib.parse.urlparse(u)
        if p.scheme != "https" or p.hostname != v["host"] or p.port or p.username or p.password:
            raise Refused(f"seq {seq}: evidence URL {u!r} is not plain https on {v['host']!r}")
    if v["host"] not in hosts or hosts[v["host"]] != v["agency"]:
        raise Refused(f"seq {seq}: {v.get('host')!r} is not a government host on the county's verify list")
    try:
        real = datetime.datetime.strptime(v["publishedDate"], "%Y-%m-%d").strftime("%Y-%m-%d") == v["publishedDate"]
    except ValueError:
        real = False
    checked = parse(v["checkedAt"])
    if not real or v["publishedDate"] > at[:10] or not checked or checked > parse(at):
        raise Refused(f"seq {seq}: evidence dates are malformed or in the future")
    if v["verifier"] not in VERIFIERS or not SHA.match(v["pageSha256"]) or not notice(v["quote"]):
        raise Refused(f"seq {seq}: evidence is not a supported verifier receipt with one supported road sentence")


def validate(ev: dict, prev_seq: int, clearance_enabled: bool, prev_eid: str = "") -> None:
    """Schema checks shared by append (issuance) and check_events (replay)."""
    if ev.get("kind") not in KINDS:
        raise Refused(f"unknown kind {ev.get('kind')!r}")
    if ev.get("seq") != prev_seq + 1:
        raise Refused(f"seq {ev.get('seq')} after {prev_seq}")
    if not parse(ev.get("at")):
        raise Refused(f"seq {ev.get('seq')}: bad at")
    if ev.get("eid") != eid_of(ev, prev_eid):
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
    if kind == "relisted" and "raisedBy" in ev:
        _check_gov(ev["seq"], ev["raisedBy"], ev["at"])
    if kind == "verified":
        _check_gov(ev["seq"], ev.get("evidence") or {}, ev["at"])
    if kind == "cleared":
        if not clearance_enabled:
            raise Refused(f"seq {ev['seq']}: clearance is disabled for this county")
        raise Refused(f"seq {ev['seq']}: no clearance verifier is installed yet")  # batch 3 replaces this


# ---- writing (the only writer) ---------------------------------------------------------------------------------

def append(new: list[dict], path: str = EVENTS, clearance_enabled: bool = False, now: datetime.datetime | None = None
           ) -> list[dict]:
    """Append the events that change the derived state; an event that would change nothing (a retry, a repeated
    outage report, an unchanged listing) is not written. Returns the events actually written."""
    history = load(path)
    state = _empty()
    for ev in history:
        apply(state, ev)
    seq = history[-1]["seq"] if history else 0
    prev = history[-1]["eid"] if history else ""
    at = iso(now or utcnow())
    written = []
    for raw in new:
        if raw.get("kind") == "cleared":
            raise Refused("cleared events come only from the verifier path")
        ev = {k: v for k, v in raw.items() if k not in ("seq", "at", "eid")}
        ev["seq"], ev["at"], ev["eid"] = seq + 1, at, eid_of(ev, prev)
        ev = {k: ev[k] for k in ("seq", "at", "eid", *sorted(k for k in ev if k not in ("seq", "at", "eid")))}
        validate(ev, seq, clearance_enabled, prev)
        before = canon(_slice(state, ev))
        apply(state, ev)
        if canon(_slice(state, ev)) == before:
            continue
        seq, prev = ev["seq"], ev["eid"]
        written.append(ev)
    if written:
        with open(path, "a", encoding="utf-8") as fh:
            for ev in written:
                fh.write(canon(ev) + "\n")
    return written


# ---- reducing --------------------------------------------------------------------------------------------------

def _merge_up(cur: dict, new: dict) -> tuple[dict, list[str]]:
    """Category and fullClosure only rise, each on its own. Scope and description (where, lanes, title, note, source)
    keep the first report's wording; a later lane description (even where the first had none) is flagged, never
    written. A record without a pin or closed stretch gains one from a later report; a known one never moves.
    Returns (merged fields, recheck reasons)."""
    merged, reasons = dict(cur), []
    sev_new, sev_cur = SEVERITY.get(new.get("status"), 1), SEVERITY.get(cur.get("status"), 1)
    if sev_new > sev_cur:
        merged["status"] = new["status"]
    elif sev_new < sev_cur:
        reasons.append("source shows reduced impact")
    if new.get("fullClosure") and not cur.get("fullClosure"):
        merged["fullClosure"] = True
    elif cur.get("fullClosure") and "fullClosure" in new and not new.get("fullClosure"):
        reasons.append("source shows reduced impact")
    if new.get("lanes") and new["lanes"] != cur.get("lanes"):  # never written, even into an empty field
        reasons.append("source changed its lane description")
    if cur.get("lat") is None and new.get("lat") is not None:  # an unlocated record gains its pin
        merged.update(lat=new["lat"], lng=new.get("lng"), approx=new.get("approx", cur.get("approx")))
    if new.get("line") or new.get("from"):
        if not (cur.get("line") or cur.get("from")):
            merged.update({k: new[k] for k in EXTENT if k in new})
        elif any(new.get(k) != cur.get(k) for k in EXTENT if k in new):
            reasons.append("source changed the closed stretch")
    return merged, reasons


def _empty() -> dict:
    return {"records": {}, "sources": {}}


def _slice(state: dict, ev: dict):
    """The part of the state an event can touch."""
    if ev["kind"] == "source":
        return state["sources"].get(ev.get("src"))
    return state["records"].get(ev.get("rid"))


def apply(state: dict, ev: dict) -> None:
    """Fold one event into the state (time-independent; derive() adds what depends on `now`)."""
    kind, rid = ev["kind"], ev.get("rid")
    recs = state["records"]
    if kind == "source":
        if state["sources"].get(ev["src"], {}).get("state") != ev["state"]:
            state["sources"][ev["src"]] = {"state": ev["state"], "since": ev["at"]}
        return
    r = recs.get(rid)
    st = parse(ev.get("stime"))
    if kind in ("reported", "relisted"):
        if r is None:
            recs[rid] = {"rid": rid, "src": ev.get("src"), "status": "active", "fields": dict(ev["fields"]),
                         "firstReported": ev["stime"], "lastReported": ev["stime"], "recheck": [], "missingSince": None,
                         "reopenedAt": None, "evidence": None, "verified": None, "raisedBy": None,
                         "statusAt": ev["stime"], "episodeStart": ev["stime"]}
            return
        if r["status"] == "reopened":
            if st and st > parse(r["reopenedAt"]):
                r.update(status="active", fields=dict(ev["fields"]), lastReported=ev["stime"], recheck=[],
                         missingSince=None, reopenedAt=None, evidence=None, verified=None, raisedBy=None,
                         statusAt=ev["stime"], episodeStart=ev["stime"])  # a new incarnation never inherits the old one's badge
            return
        gov = ev.get("raisedBy")
        if gov and (gov["publishedDate"] < _episode(r) or supports(r["fields"], gov["quote"], _raise_of(ev["fields"]))):
            return  # a cited raise must be about this road and this episode
        was = rank(r["fields"])
        r["fields"], reasons = _merge_up(r["fields"], ev["fields"])
        if rank(r["fields"]) > was:  # remember which observation supports the current impact
            r["statusAt"], r["raisedBy"] = ev["stime"], ev.get("raisedBy")
            if ev.get("raisedBy"):
                r["belowRaise"], r["raiseBase"] = was, r["lastReported"]
        if (r.get("raisedBy") and not ev.get("raisedBy") and rank(ev["fields"]) >= tuple(r.get("belowRaise") or ())
                and st and st <= parse(r.get("raiseBase"))):
            # the original report repeating its own weaker wording is not new reduced impact
            reasons = [x for x in reasons if x != "source shows reduced impact"]
        if st and st > parse(r["lastReported"]):
            r["lastReported"] = ev["stime"]
        r["missingSince"] = None
        for reason in reasons:
            if reason not in r["recheck"]:
                r["recheck"].append(reason)
    elif r is None:
        return
    elif kind == "missing":
        if r["status"] == "active" and not r["missingSince"]:
            r["missingSince"] = ev["at"]
    elif kind == "recheck":
        if r["status"] == "active" and ev["reason"] not in r["recheck"]:
            r["recheck"].append(ev["reason"])
    elif kind == "verified":  # the newest official statement wins; earlier ones stay in the history
        v = ev["evidence"]
        if r["status"] != "active" or v["publishedDate"] < _episode(r) or supports(r["fields"], v["quote"]):
            return
        if not r.get("verified") or ev["evidence"]["publishedDate"] >= r["verified"]["publishedDate"]:
            r["verified"] = ev["evidence"]
    elif kind == "cleared":
        r.update(status="reopened", reopenedAt=ev["evidence"]["reopenedAt"], evidence=ev["evidence"])


def _episode(r: dict) -> str:
    return (r.get("episodeStart") or r["firstReported"])[:10]


def _raise_of(fields: dict) -> str:
    return "full" if fields.get("fullClosure") else "closed"


def derive(events: list[dict], now: datetime.datetime | None = None) -> dict:
    """Road state at `now`: {"records": {rid: state}, "sources": {src: health}}."""
    now = now or utcnow()
    state = _empty()
    for ev in events:
        apply(state, ev)
    for r in state["records"].values():
        if r["status"] == "reopened":
            r["show"] = "green" if now < parse(r["reopenedAt"]) + datetime.timedelta(hours=GREEN_HOURS) else "hidden"
        else:
            r["show"] = "active"
            old = now - parse(r["lastReported"]) > datetime.timedelta(hours=RECHECK_AFTER_HOURS)
            r["awaitingOfficial"] = bool(r["missingSince"] or r["recheck"] or old)
    return state
