#!/usr/bin/env python3
"""Closure history: the only writer of closure_events.jsonl and the reducer that derives road state from it.

Reports leave through explicitly enabled clearance events: an official reopening statement, an approved feed-drop
rule, or the bounded Nov4 administrative cleanup. Only the official statement confirms reopening; the cleanup is
hidden immediately and keeps the source history. Time and inferred travel speed never reopen a report.

Each line of closure_events.jsonl is one event: {seq, at, eid, rid, kind, actor, src, stime, ...}.
  rid    record id, "<county>:<source>:<key>"; one source report per record, records are never merged
  src    source key (fl511, fhp, manual, nws ...); stime is the source's own time for this observation
Kinds:
  reported   a record first appears (carries `fields`); reactivates a cleared record only if newer than its reopening
  relisted   the source still lists it; may raise severity or scope, never lower it (a lower reading flags recheck).
             With `raisedBy` (host, url, agency, quote) it is a government page's own closure statement (Mike, Nov 3:
             "go by what the government page says and cite it"); it can only raise. A manual report may carry
             `supportedBy` (source, url, published, quote): the dated, cited list or article that raised it to closed
             (Mike, Nov 3: "yes, adds only"). It only raises and is shown with its citation; the first report stays.
  missing    the source no longer lists it: flag only
  recheck    a reason to look again: flag only
  source     per-source health (`state`: up, down, empty, partial = some rows unreadable): no record changes
  verified   a government page confirms the report (`evidence`: host, url, agency, quote, publishedAt): a badge
             only, never a status change; the newest statement is shown
  located    a manual report's pin was wrong; moved to the place its own words name (`fields`: lat, lng, approx;
             `locatedBy`: source, url, checked, note), e.g. the county road map's intersection. Never changes status;
             the old position is kept in the record and the closed stretch is redrawn from the new pin
  cleared    the record comes off the active map, for one of the county's enabled clearance kinds: "fhp-dropped" (a plain
             FHP crash FHP stopped listing; history before Nov 4), "feed-dropped" (anything FHP or FL511 stopped listing,
             closures included; Mike, Nov 4) or "official" (a government page on the verify list says the road is open,
             as one supported sentence, written by verify.py --open; Mike, Nov 3: only an official statement on a
             credible website may show a road reopened); "owner-cleanup" is the bounded Nov4 administrative removal, hidden immediately
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
KINDS = ("reported", "relisted", "missing", "recheck", "source", "verified", "cleared", "located")
GREEN_HOURS = 24
from zoneinfo import ZoneInfo  # noqa: E402
ET = ZoneInfo("America/New_York")  # government posts state Eastern times
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
# The only sentence shapes accepted as a government statement that a road is open. A trailing shoulder caveat (the
# City of Melbourne's "..., but there are some infrastructure issues on the shoulder") is allowed and quoted on the
# page; nothing else may follow.
OPEN = [(re.compile(rf"^{_R} at {_X} is (?:now )?open\.?$"), False),  # first, so "X at Y" is never read as one road
        (re.compile(rf"^(?:All of )?{_R} is (?:now )?open(?:, but there (?:is|are) (?:some )?[a-z ]{{3,60}} on the shoulder)?\.?$"), True),
        (re.compile(rf"^{_R} has reopened\.?$"), True)]
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


def opening(sentence: str) -> dict | None:
    """Parse one supported government open sentence: {road, cross, whole} or None."""
    for rx, whole in OPEN:
        m = rx.match(sentence.strip())
        if m:
            g = m.groupdict()
            return {"road": road_words(g["road"]), "cross": road_words(g.get("cross") or ""), "whole": whole}
    return None


def opens(fields: dict, quote: str) -> str | None:
    """None when the open sentence is about THIS report's road (and its cross street, unless it covers the whole road)."""
    n = opening(quote)
    if not n:
        return "the sentence is not one of the supported road-open forms"
    road = road_words(re.split(r"\s*\(|/", fields.get("roadShort") or fields.get("road") or "")[0])
    if not road or n["road"] != road:
        return f"the sentence is about {n['road']!r}, not this road ({road or 'unknown'})"
    spot = fields.get("spot") or (fields.get("where") or "").partition(" at ")[2]
    cross = road_words(re.sub(r"^(?:at|near|from)\s+", "", spot, flags=re.I).split(" to ")[0])
    if not n["whole"] and (not cross or n["cross"] != cross):
        return f"the sentence is about {n['cross']!r}, not this report's cross street ({cross or 'none recorded'})"
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
    for k in ("raisedBy", "supportedBy", "locatedBy"):  # added later; only hashed where present so earlier eids are unchanged
        if k in ev:
            core[k] = ev[k]
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


GOV_FIELDS = ("host", "url", "finalUrl", "agency", "quote", "publishedDate", "checkedAt", "pageSha256", "postSha256",
              "verifier")


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
    closed = bool(raise_to) or fields.get("status") == "closed" or bool(fields.get("fullClosure"))  # the page's test
    if n["impact"] == "closed" and not closed:
        return "the sentence says closed but the report is not a closure (use --closed to raise it)"
    if n["impact"] == "flooded" and (closed or fields.get("status") != "flooded"):
        return "a flooding sentence supports a flood report only, never one shown closed"
    return None


SUPPORT_FIELDS = ("source", "url", "published", "quote")


def _plain_citation(seq, v: dict, fields: tuple, what: str) -> None:
    """Exactly these string fields, road wording only (the page's privacy rule, Mike Nov 2), a plain https link with a host."""
    if not isinstance(v, dict) or set(v) != set(fields) or not all(isinstance(v[k], str) and v[k].strip() for k in v):
        raise Refused(f"seq {seq}: {what} needs exactly {', '.join(fields)}")
    if any(SENSITIVE.search(v[k]) or CRASH.search(v[k]) for k in fields if k != "url"):
        raise Refused(f"seq {seq}: {what} carries personal or crash detail; citations are road wording only")
    p = urllib.parse.urlparse(v["url"])
    if p.scheme != "https" or not p.hostname or "." not in p.hostname or p.port or p.username or p.password:
        raise Refused(f"seq {seq}: {what} link must be plain https with a real host")


def _check_support(ev: dict) -> None:
    """A cited raise of a manual report: every field present, a plain https link, a publish time not after the event."""
    v, seq = ev["supportedBy"], ev["seq"]
    if ev["kind"] not in ("reported", "relisted") or ev.get("src") != "manual":
        raise Refused(f"seq {seq}: supportedBy is only for manual reports")
    _plain_citation(seq, v, SUPPORT_FIELDS, "supportedBy")
    if not parse(v["published"]) or v["published"] > ev["at"]:
        raise Refused(f"seq {seq}: supportedBy needs a publish time not after the event")


LOCATED_FIELDS = ("source", "url", "checked", "note")
BREVARD_BOX = (27.8, 28.8, -81.1, -80.4)  # lat min/max, lng min/max


def _check_located(ev: dict) -> None:
    """A pin correction: manual reports only, a position inside Brevard, a cited https source checked before the event."""
    v, f, seq = ev.get("locatedBy"), ev.get("fields"), ev["seq"]
    if ev.get("src") != "manual" or ":manual:" not in ev.get("rid", ""):
        raise Refused(f"seq {seq}: only manual reports are relocated")
    if not isinstance(f, dict) or set(f) - {"lat", "lng", "approx"} or not all(
            isinstance(f.get(k), (int, float)) and not isinstance(f.get(k), bool) for k in ("lat", "lng")):
        raise Refused(f"seq {seq}: located needs fields lat, lng (and optional approx) only")
    lo, hi, w, e = BREVARD_BOX
    if not (lo <= f["lat"] <= hi and w <= f["lng"] <= e):
        raise Refused(f"seq {seq}: located position is outside Brevard")
    _plain_citation(seq, v, LOCATED_FIELDS, "locatedBy")
    if not parse(v["checked"]) or v["checked"] > ev["at"]:
        raise Refused(f"seq {seq}: locatedBy needs a check time not after the event")


def _check_gov(seq, v: dict, at: str, sentence=None) -> None:
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
    if (v["verifier"] not in VERIFIERS or not SHA.match(v["pageSha256"]) or not SHA.match(v["postSha256"])
            or not (sentence or notice)(v["quote"])):
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
    if kind == "source" and ev.get("state") not in ("up", "down", "empty", "partial"):
        raise Refused(f"seq {ev['seq']}: bad source state")
    if kind == "recheck" and not ev.get("reason"):
        raise Refused(f"seq {ev['seq']}: recheck needs a reason")
    if kind == "relisted" and "raisedBy" in ev:
        _check_gov(ev["seq"], ev["raisedBy"], ev["at"])
    if "supportedBy" in ev:
        _check_support(ev)
    if kind == "located":
        _check_located(ev)
    if kind == "verified":
        _check_gov(ev["seq"], ev.get("evidence") or {}, ev["at"])
    if kind == "cleared":
        if not clearance_enabled:
            raise Refused(f"seq {ev['seq']}: clearance is disabled for this county")
        if (ev.get("evidence") or {}).get("kind") == "official":
            _check_official(ev)
        elif (ev.get("evidence") or {}).get("kind") == "owner-cleanup":
            _check_owner_cleanup(ev)
        elif (ev.get("evidence") or {}).get("kind") == "feed-dropped":
            _check_feed_drop(ev)
        else:
            _check_fhp_drop(ev)


FHP_URL = "https://trafficincidents.flhsmv.gov/SmartWebClient/CadView.aspx"


def fhp_clearable(fields: dict) -> bool:
    """Mike (Nov 3): "we can clear crashes when they come off the FHP website, just not road closures" and "make sure
    that there is never a 'no longer listed' that is still actually closed". Only an FHP crash (MVC) that was never a
    closure can come off the map when FHP drops it; a water closure, a roadblock, debris or anything closed stays until
    a cited source says the road is open. Applied when history is read, so an earlier drop of a closure never counts."""
    closed = fields.get("status") == "closed" or bool(fields.get("fullClosure"))
    return not closed and bool(re.search(r"\b(?:MVC)+\b", fields.get("title") or ""))  # older titles read "MVCMVC"


def _check_fhp_drop(ev: dict) -> None:
    """The only clearance installed (Mike, Nov 3, "ok if FHP drops it, lets clear it for now", then narrowed to crashes
    only): an FHP crash the FHP live CAD page no longer lists, seen gone on two successful fetches. Its evidence names the FHP page, when the
    crash was first missing, and when it was confirmed gone (the clearance time). Nothing else can be cleared."""
    v = ev.get("evidence") or {}
    keys = {"kind", "agency", "url", "missingSince", "reopenedAt"}
    if (set(v) != keys or v["kind"] != "fhp-dropped" or v["url"] != FHP_URL or v["agency"] != "Florida Highway Patrol"
            or ev.get("src") != "fhp" or not str(ev.get("rid", "")).split(":")[1:2] == ["fhp"]
            or not parse(v["missingSince"]) or v["reopenedAt"] != ev["at"] or parse(v["missingSince"]) >= parse(ev["at"])):
        raise Refused(f"seq {ev['seq']}: the only clearance is an FHP incident the FHP page stopped listing")


def independent(r: dict) -> bool:
    """A record carrying evidence of its own besides its feed: a government page that raised or confirmed its impact
    (raisedBy, supportedBy) or verified it. A feed drop removes only the feed's part, so such a record stays active and
    the page shows it kept by that evidence (reconcile.keep_independent, Codex FL review)."""
    return bool(r.get("raisedBy") or r.get("supportedBy") or r.get("verified"))


FEED_DROP = {"fhp": ("Florida Highway Patrol", FHP_URL), "fl511": ("FL511 (FDOT)", "https://fl511.com")}


def _check_feed_drop(ev: dict) -> None:
    """Mike (Nov 4 2026): "if FHP/FL511 drop something that was previously there, we drop it too." Any report from the
    FHP CAD page or FL511, closures included, that its feed no longer lists, seen gone on two successful fetches. Its
    evidence names that feed, when the report was first missing, and when it was confirmed gone. Florida only: the
    county's clearance kinds enable it, and nj/ny never list it."""
    v = ev.get("evidence") or {}
    src = ev.get("src")
    keys = {"kind", "agency", "url", "missingSince", "reopenedAt"}
    if (set(v) != keys or v["kind"] != "feed-dropped" or src not in FEED_DROP or (v["agency"], v["url"]) != FEED_DROP[src]
            or not str(ev.get("rid", "")).split(":")[1:2] == [src]
            or not parse(v["missingSince"]) or v["reopenedAt"] != ev["at"] or parse(v["missingSince"]) >= parse(ev["at"])):
        raise Refused(f"seq {ev['seq']}: a feed drop clears only a report its own FHP or FL511 feed stopped listing")


def _check_owner_cleanup(ev):
    import owner_cleanup as O
    v=ev.get("evidence") or {}
    if (set(v)!={"kind","cutoff","rule","checkedAt","reopenedAt"} or v.get("kind")!="owner-cleanup"
            or v.get("cutoff")!=O.CUTOFF or v.get("rule")!=O.RULE or v.get("reopenedAt")!=ev["at"]
            or ev.get("src") in FEED_DROP or str(ev.get("rid","")).split(":")[0] not in O.COUNTIES
            or parse(ev["at"])<=parse(O.CUTOFF) or not parse(v.get("checkedAt"))
            or not 0 <= (parse(ev["at"])-parse(v["checkedAt"])).total_seconds() <= 300):
        raise Refused("owner cleanup is limited to the approved old non-feed Florida reports")


def official_time(v: dict) -> str:
    """When the government says the road opened: the post's own stated time (sourceTime), else the start of its
    publish day in Eastern time, so a date-only statement never looks later than it may be."""
    if v.get("sourceTime"):
        return v["sourceTime"]
    try:
        return iso(datetime.datetime.strptime(v["publishedDate"], "%Y-%m-%d").replace(tzinfo=ET))
    except (KeyError, TypeError, ValueError):
        return ""  # malformed evidence: validation refuses it


def _check_official(ev: dict) -> None:
    """A government reopening: a full verifier receipt (as for a verified badge) whose quote is one supported open
    sentence, plus the post's own stated time when it gives one (sourceTime, on its publish day, not after the event).
    The reopening time is that statement's time, never when we found it (Codex #183)."""
    v = dict(ev.get("evidence") or {})
    if set(v) != {"kind", "reopenedAt", "sourceTime", *GOV_FIELDS} or v.pop("kind") != "official":
        raise Refused(f"seq {ev['seq']}: an official reopening needs exactly a verifier receipt, its source time and reopening time")
    st, ro = v.pop("sourceTime"), v.pop("reopenedAt")
    if st and (not parse(st) or parse(st) > parse(ev["at"])
               or parse(st).astimezone(ET).strftime("%Y-%m-%d") != v["publishedDate"]):
        raise Refused(f"seq {ev['seq']}: the source time must be on the post's publish day and not after the event")
    _check_gov(ev["seq"], v, ev["at"], sentence=opening)
    if ro != official_time({**v, "sourceTime": st}):
        raise Refused(f"seq {ev['seq']}: an official reopening is dated by the statement, not when it was found")


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
        if raw.get("kind") == "cleared" and (raw.get("evidence") or {}).get("kind") not in ("fhp-dropped", "feed-dropped", "owner-cleanup", "official"):
            raise Refused("cleared events come only from the FHP/FL511 drop rules or the verifier's --open path")
        ev = {k: v for k, v in raw.items() if k not in ("seq", "at", "eid")}
        if ev["kind"] == "cleared":  # an FHP drop is dated when written; an official reopening by the statement
            ev["evidence"] = dict(ev["evidence"], reopenedAt=official_time(ev["evidence"])
                                  if ev["evidence"].get("kind") == "official" else at)  # a feed drop is dated when written
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
                         "supportedBy": ev.get("supportedBy"), "statusAt": ev["stime"], "episodeStart": ev["stime"]}
            return
        if r["status"] == "reopened":
            # an FHP incident listed again after it dropped off is active again (its source time is the old Received time)
            again = (r["evidence"] or {}).get("kind") in ("fhp-dropped", "feed-dropped") and parse(ev["at"]) > parse(r["reopenedAt"])
            if (r.get("evidence") or {}).get("kind")=="owner-cleanup":
                again=bool(st and st>parse(r["evidence"]["cutoff"]))
            if again or (st and st > parse(r["reopenedAt"])):
                r.update(status="active", fields=dict(ev["fields"]), lastReported=ev["stime"], recheck=[],
                         missingSince=None, reopenedAt=None, evidence=None, verified=None, raisedBy=None,
                         supportedBy=ev.get("supportedBy"), statusAt=ev["stime"], episodeStart=ev["stime"])  # a new incarnation never inherits the old one's badge
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
            r["supportedBy"] = ev.get("supportedBy")  # the citation for the raise, or none
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
    elif kind == "located":
        if r["status"] == "active":
            old = {k: r["fields"].get(k) for k in ("lat", "lng")}
            for k in EXTENT:
                r["fields"].pop(k, None)  # the stretch drawn from the wrong pin goes; the next report redraws it
            r["fields"].update(ev["fields"])
            r["located"] = dict(ev["locatedBy"], at=ev["at"], fromLat=old["lat"], fromLng=old["lng"])
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
        v = ev["evidence"]
        if v.get("kind")=="owner-cleanup":
            import owner_cleanup as O
            if not O.safe_to_remove(r,state):return
        if v.get("kind") == "fhp-dropped" and not (r["status"] == "active" and r["src"] == "fhp"
                                                    and r["missingSince"] == v["missingSince"]
                                                    and fhp_clearable(r["fields"])):
            return  # only a still-missing FHP crash, gone since the time its evidence says (never a road closure)
        if v.get("kind") == "feed-dropped" and not (r["status"] == "active" and FEED_DROP.get(r["src"], (None,))[0] == v.get("agency")
                                                     and r["missingSince"] == v["missingSince"]):
            return  # only a report still missing from its own feed since the time its evidence says
        if v.get("kind") in ("feed-dropped", "fhp-dropped") and independent(r):
            return  # a government page raised, supported or confirmed it: that evidence stands without the feed
        if v.get("kind") == "official" and (r["status"] != "active" or opens(r["fields"], v["quote"])
                                            or not official_after(v, r["lastReported"])):
            return  # an open statement must be about this road and newer than its latest report
        r.update(status="reopened", reopenedAt=v["reopenedAt"], evidence=v)


def official_after(v: dict, latest: str) -> bool:
    """Is the open statement newer than the latest report of the restriction? With the post's stated time, compare
    times; with only a date, the latest report must be from an earlier day (a same-day report is ambiguous: kept)."""
    if v.get("sourceTime"):
        return parse(v["sourceTime"]) > parse(latest)
    return parse(latest).astimezone(ET).strftime("%Y-%m-%d") < v["publishedDate"]


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
            r["show"] = "green" if (r.get("evidence") or {}).get("kind")!="owner-cleanup" and now < parse(r["reopenedAt"]) + datetime.timedelta(hours=GREEN_HOURS) else "hidden"
        else:
            r["show"] = "active"
            old = now - parse(r["lastReported"]) > datetime.timedelta(hours=RECHECK_AFTER_HOURS)
            r["awaitingOfficial"] = bool(r["missingSince"] or r["recheck"] or old)
    return state
