#!/usr/bin/env python3
"""Turn this run's observations into closure history events, then write flood_data.json from the derived state.

  python3 reconcile.py      reads observations.json (from update_flood_data.py), county.json, closure_events.jsonl

A record seen this run is `reported` (new) or `relisted`; an active record its source no longer lists is `missing`
(a flag, never a status change). Source health is recorded on change. Clearance is not issued here: county clearance
is disabled until the verifier and the reopened display qualify (Phase 1 batches 3 and 4).
"""
from __future__ import annotations

import datetime
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


FHP_COLUMNS = ("Incident Type", "Received", "Dispatched", "Arrived", "County", "Location", "Remarks", "Latitude",
               "Longitude")


def fhp_table(page: str) -> list[dict]:
    """Every incident row of the FHP live CAD table (all counties), as dicts keyed by the table's own header names.
    The columns are bound by header text, never by position: the page has Received, Dispatched and Arrived times and
    NO cleared time (an incident leaves the page when it is over). Raises ValueError when the expected headers are
    missing, or when any incident row in that table is short or lacks its type, time, county or location: a broken
    row must make the whole snapshot a source failure, never look like an incident that has gone (Codex #105)."""
    import html as _html
    clean = lambda c: re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).replace("\xa0", " ").strip()
    heads = list(dict.fromkeys(clean(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", page, flags=re.S | re.I)))
    if not all(c in heads for c in FHP_COLUMNS):
        raise ValueError(f"FHP CAD headers changed: {heads[:12]}")
    idx = {c: heads.index(c) for c in FHP_COLUMNS}
    out = []
    # incident rows are the grid's data rows (class dxgvDataRow); layout rows elsewhere on the page are not
    for row in re.findall(r"<tr[^>]*class=\"[^\"]*\bdxgvDataRow\b[^\"]*\"[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
        if len(cells) != len(FHP_COLUMNS) or max(idx.values()) >= len(cells):
            raise ValueError(f"FHP CAD incident row has {len(cells)} cells, expected {len(FHP_COLUMNS)}")
        r = {c: cells[i] for c, i in idx.items()}
        if not all(r[c] for c in ("Incident Type", "Received", "County", "Location")):
            raise ValueError("FHP CAD incident row is missing its type, time, county or location")
        try:  # the Received time is the incident's identity; an unreadable one fails the snapshot (Codex #107)
            datetime.datetime.strptime(r["Received"], "%m/%d/%Y %H:%M:%S")
        except ValueError:
            raise ValueError(f"FHP CAD incident row has an unreadable Received time {r['Received']!r}") from None
        out.append(r)
    return out


def fhp_rows(page: str, county: str = "BREVARD") -> list[dict]:
    """The county's rows of the FHP live CAD table (see fhp_table; raises ValueError on a changed or broken table)."""
    return [r for r in fhp_table(page) if r["County"].upper() == county]


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


LSR_FLOOD = ("FLOOD", "FLASH FLOOD", "COASTAL FLOOD")
_LSR_HEAD = re.compile(r"^(\d{4}) ([AP]M) {2,}")
_W = r"[A-Z0-9]\w*"  # a capitalised word; names stop at a lower-case word or a full stop
_LSR_AT = re.compile(rf"\b(?:at|near|on)\s+(?:at\s+)?({_W}(?: {_W})*?)\s+(?:&|and|at)\s+({_W}(?: {_W})*)")
# The only remark sentences read as a closure: a whole sentence whose closure clause carries its own intersection.
# "Media reports a road closure at Helen St & Minton Rd due to flooding." / "Road closure at X & Y."
_LSR_CLOSED = re.compile(rf"^(?:(?:{_W}(?: {_W})*|Media|Police|Law enforcement) (?:reports?|reported|has reported) a "
                         rf"|)[Rr]oad closure (?:at|near) (?:at )?({_W}(?: {_W})*?) (?:&|and) ({_W}(?: {_W})*)"
                         rf"(?: due to flooding)?\.?$")
_LSR_IS = re.compile(rf"^({_W}(?: {_W})* (?:St|Rd|Ave|Blvd|Dr|Ln|Pkwy|Hwy|Way|Ct|Pl|Cswy|Street|Road|Avenue)"
                     rf"(?: [NS][EW])?) is flooded")


def lsr_entries(text: str, county: str = "BREVARD", state: str = "FL") -> list[dict]:
    """Flood entries from an NWS Local Storm Report product (fixed columns: time, event, city, lat/lon; then date,
    magnitude, county, state, source; then indented remarks). Other counties and other event types are skipped."""
    lines, out = text.splitlines(), []
    for i, line in enumerate(lines[:-1]):
        m, two = _LSR_HEAD.match(line), lines[i + 1]
        ll = re.search(r"(\d+\.\d+)N\s+(\d+\.\d+)W\s*$", line)
        if not m or not ll or not re.match(r"\d\d/\d\d/\d{4}", two):
            continue
        event, city = line[12:29].strip(), line[29:53].strip()
        if event.upper() not in LSR_FLOOD or two[29:48].strip().upper() != county or two[48:50] != state:
            continue
        remarks = []
        for r in lines[i + 3:]:
            if not r.startswith("            ") or not r.strip():
                break
            remarks.append(r.strip())
        out.append({"time": f"{two[:10]} {m.group(1)} {m.group(2)}", "event": event.title(), "city": city,
                    "lat": float(ll.group(1)), "lng": -float(ll.group(2)), "source": two[53:].strip(),
                    "remarks": " ".join(remarks)})
    return out


def lsr_item(e: dict, when: str, url: str) -> dict:
    """An observation from one LSR flood entry. NWS publishes the report (with who reported it); a remark saying a
    road is closed shows the road closed, otherwise flooded. The NWS point is rounded, so the spot comes from the
    remark's intersection when it names one."""
    rem, near = e["remarks"], re.sub(r"^\d+ [NSEW]{1,3} ", "", e["city"])
    # closed only from a whole supported closure sentence; its own intersection is the spot. Anything else stays flooded.
    c = next(filter(None, (_LSR_CLOSED.match(s) for s in re.split(r"(?<=\.)\s+", rem))), None)
    closed = c is not None
    m = c or _LSR_AT.search(rem)
    if m:
        road, spot = m.group(1), "at " + m.group(2)
    else:
        m = _LSR_IS.search(rem)
        road, spot = (m.group(1), f"in {near}") if m else (f"{near} area", "")
    # same minute and rounded point can hold two places; the named place keeps them apart, exact repeats still match
    key = f"{e['time']}|{e['lat']:.2f}|{e['lng']:.2f}|{E.road_words(road)}|{E.road_words(spot)}"
    return {"id": "nws-" + hashlib.sha1(key.encode()).hexdigest()[:10], "layer": "NWS", "status": "closed" if closed else "flooded",
            "fullClosure": False, "road": road, "roadShort": road, "spot": spot, "where": f"{road} {spot}".strip(),
            "lat": e["lat"], "lng": e["lng"],
            "title": f"{e['event']} reported to NWS Melbourne ({e['source']})", "note": rem, "city": near, "approx": True,
            "reported": when, "updated": when, "source": f"NWS Melbourne storm report ({e['source']})", "sourceUrl": url}


def rid_of(it: dict, county: str = "brevard") -> str:
    """Stable record ids. FHP: reported time + hash of road and cross street (the old id used Python's per-process
    hash, which changes between runs)."""
    layer, i = it.get("layer"), str(it.get("id", ""))
    if layer == "manual":
        return f"{county}:manual:" + i.removeprefix("man-")
    if layer == "FHP":
        key = hashlib.sha1(f"{it.get('road', '')}|{it.get('spot', '')}".encode()).hexdigest()[:8]
        return f"{county}:fhp:" + re.sub(r"\D", "", it.get("reported") or "") + "-" + key
    if layer == "NWS":
        return f"{county}:nws:" + i.removeprefix("nws-")
    return f"{county}:fl511:" + i.removeprefix("fl511-")


def src_of(it: dict) -> str:
    return {"manual": "manual", "FHP": "fhp", "NWS": "nws"}.get(it.get("layer"), "fl511")


def fields_of(it: dict) -> dict:
    return {k: it[k] for k in E.FIELDS if k in it and it[k] not in (None, "")}


def stime_of(it: dict, fallback: str = "") -> str:
    for k in ("updated", "reported"):
        if E.parse(it.get(k)):
            return it[k]
    return fallback


def plan(obs: dict, state: dict, now: str, county: str = "brevard", fhp_clears: bool = False
         ) -> tuple[list[dict], list[str]]:
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
        ev = {"rid": rid, "kind": kind, "src": src_of(it), "actor": "reconcile", "stime": stime_of(it, now),
              "fields": fields_of(it)}
        if it.get("raisedPer") and ev["src"] == "manual":  # the cited list that raised a manual report to closed
            ev["supportedBy"] = {k: it["raisedPer"].get(k) for k in E.SUPPORT_FIELDS}
        r = state["records"].get(rid)
        if (it.get("locatedPer") and ev["src"] == "manual" and r and r["status"] == "active"
                and (r["fields"].get("lat"), r["fields"].get("lng")) != (it.get("lat"), it.get("lng"))):
            # a wrong pin corrected first, so this run's report (and its closed stretch) lands on the right place
            new.append({"rid": rid, "kind": "located", "src": "manual", "actor": "reconcile", "stime": now,
                        "fields": {k: it[k] for k in ("lat", "lng", "approx") if k in it},
                        "locatedBy": {k: it["locatedPer"].get(k) for k in E.LOCATED_FIELDS}})
        new.append(ev)
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
            elif (r["rid"] not in observed and src == "fhp" and fhp_clears and health == "up"
                  and E.fhp_clearable(r["fields"])):
                # Mike (Nov 3), for now: an FHP crash (never a closure) still gone on the next successful fetch is cleared, citing FHP
                new.append({"rid": r["rid"], "kind": "cleared", "src": src, "actor": "reconcile", "stime": now,
                            "evidence": {"kind": "fhp-dropped", "agency": FEEDS["fhp"]["agency"], "url": E.FHP_URL,
                                         "missingSince": r["missingSince"], "reopenedAt": ""}})
    return new, notes


_OPEN_WORDS = re.compile(r"\b(?:ro\w*d(?:way)?\s+)?(?:open|clear(?:ed)?)\b", re.I)


FEEDS = {"fl511": {"agency": "FL511 (FDOT)", "url": "https://fl511.com"},
         "fhp": {"agency": "Florida Highway Patrol", "url": "https://trafficincidents.flhsmv.gov/SmartWebClient/CadView.aspx"}}
LISTED = ("fl511", "fhp")  # live listings, where dropping off means something; NWS storm reports simply age off
# NWS storm reports are preliminary and relay reports from media, spotters, the public and officials, so they show as
# "Reported, not government verified" with the original reporter named; they are not in FEEDS (Codex #73).


def verification(r: dict) -> dict | None:
    """Government verified: the report came from a government feed itself, or a government page confirmed it
    (a `verified` event). Otherwise None: shown as reported, not government verified."""
    if r["src"] in FEEDS and not r.get("raisedBy"):  # dated by the feed observation that set the current impact
        return dict(FEEDS[r["src"]], kind="feed", publishedAt=r.get("statusAt") or r["firstReported"])
    if r.get("verified"):
        return dict(r["verified"], kind="page")
    return None


def display(state: dict) -> tuple[list[dict], list[dict]]:
    """Active records in the page's existing item schema, plus reopened records still inside their green window."""
    items, reopened = [], []
    for r in state["records"].values():
        it = dict(r["fields"], id=r["rid"], updated=r["lastReported"], lastReported=r["lastReported"],
                  firstReported=r["firstReported"])
        it["verified"] = verification(r)
        if r.get("raisedBy"):
            it["raisedBy"] = r["raisedBy"]
        elif r.get("supportedBy"):
            it["supportedBy"] = r["supportedBy"]
        if r.get("located"):
            it["located"] = r["located"]
        if r["status"] == "active":
            it.update(awaitingOfficial=r["awaitingOfficial"], missingSince=r["missingSince"], recheck=r["recheck"])
            if r["missingSince"] and r["src"] in LISTED:
                # Mike: a feed dropping a closure is cited but stays a flag; it is not an official reopening.
                f = r["fields"]
                it["noLongerListed"] = dict(FEEDS[r["src"]], since=r["missingSince"],
                                            wasClosed=f.get("status") == "closed" or bool(f.get("fullClosure")))
            if it.get("lanes") and _OPEN_WORDS.search(it["lanes"]):
                # An old source remark such as "Roadway Open" is not a verified reopening; show it as a dated note.
                it["lanes"] = f"Source said \"{it['lanes']}\" ({r['firstReported'][:16].replace('T', ' ')} UTC); not a verified reopening"
            items.append(it)
        elif r["show"] == "green":  # cleared in the last day: listed with its citation (a former closure also gets a green pin)
            it.update(reopenedAt=r["reopenedAt"], evidence=r["evidence"])
            reopened.append(it)
    reopened.sort(key=lambda it: it["reopenedAt"], reverse=True)
    items.sort(key=lambda it: it["id"])
    items = keep_independent(merge_nws(items))
    for c in reopened:
        # Another source's report at the same place is never cleared by FHP's drop; it stays on the map (how the two
        # should look together is Mike's open choice). Named here, with whether its own feed still lists it.
        c["stillReported"] = [{"id": a["id"], "where": a.get("where") or a.get("road") or a.get("title"),
                               "source": a.get("source"), "since": (a.get("noLongerListed") or {}).get("since")}
                              for a in items if same_spot(c, a)]
    return items, reopened


def spot_key(it: dict) -> tuple | None:
    """place() with the road's direction and mile-marker tags taken out ('I-95 SB [MM222] at Mm222' and 'I-95 SB at
    MM 222' are one place; 'N Wickham Rd' and 'Wickham Rd SB' are one road), plus the direction when one is given."""
    where_road, _, where_cross = (it.get("where") or "").partition(" at ")
    raw = re.split(r"\s*\(|/", it.get("roadShort") or it.get("road") or where_road)[0]
    d = re.search(r"\b(NB|SB|EB|WB)\b", raw + " " + where_road + " " + (it.get("dir") or ""), re.I)
    norm = lambda x: re.sub(r"\bmm (\d+)\b", r"mm\1", re.sub(r"^(?:n|s|e|w|north|south|east|west) | (?:nb|sb|eb|wb)$", "",
                            E.road_words(re.sub(r"\[[^\]]*\]", " ", x))))
    road = norm(raw)
    cross = re.sub(r"^(?:at|near|between)\s+", "", it.get("spot") or where_cross, flags=re.I)
    cross = norm(re.split(r"\s+(?:and|to)\s+|\s*\(", cross)[0])
    if not road or not cross or road == cross:
        return None
    return frozenset((road, cross)), d.group(1).upper() if d else None


def same_spot(a: dict, b: dict) -> bool:
    """Two reports at one place: the same intersection or mile marker by name (either order), and not opposite
    directions. Nearness alone never makes two differently named places one."""
    ka, kb = spot_key(a), spot_key(b)
    return bool(ka and kb and ka[0] == kb[0] and (not ka[1] or not kb[1] or ka[1] == kb[1]))


def place(it: dict) -> frozenset | None:
    """The intersection a pin is at, as the two road names in either order ('Minton Rd at Helen St' is the same place
    as 'Helen St at Minton Rd'); None when there is no cross street to match on."""
    where_road, _, where_cross = (it.get("where") or "").partition(" at ")
    road = E.road_words(re.split(r"\s*\(|/", it.get("roadShort") or it.get("road") or where_road)[0])
    road = re.sub(r" (?:nb|sb|eb|wb)$", "", road)
    cross = re.sub(r"^(?:at|near|between)\s+", "", it.get("spot") or where_cross, flags=re.I)
    cross = E.road_words(re.split(r"\s+(?:and|to)\s+|\s*\(", cross)[0])
    return frozenset((road, cross)) if road and cross and road != cross else None


# NWS storm report sources that are government agencies (Mike, Nov 3): only these get the check and "Government
# verified". Public, media, spotters, amateur radio and social media stay green "NWS storm report" with no check.
NWS_GOV = {"law enforcement", "emergency mngr", "emergency manager", "nws employee", "official nws obs",
           "nws storm survey", "dept of highways", "local official", "county official", "state official",
           "other federal", "other federal agency", "coast guard", "park/forest srvc"}
# Fire Dept/Rescue is left out: fire and rescue services can be contract or private (USFA registry), so the category
# alone does not prove a government agency (Codex #96).


def nws_gov(reporter: str) -> bool:
    return re.sub(r"\s+", " ", (reporter or "").strip().lower()) in NWS_GOV


def merge_nws(items: list[dict]) -> list[dict]:
    """Mike (Nov 3): one pin per place. An NWS storm report at the same intersection as another pin joins that pin
    (listed first in it, as an NWS storm report) instead of adding a second pin. The pin shows the more serious status
    of the two; nothing in the history changes. Each pin carries its NWS reports in 'nws', newest first."""
    hosts, out = {}, []
    for it in sorted(items, key=lambda i: i["id"].split(":")[1] == "nws"):  # other pins claim their place first
        nws = it["id"].split(":")[1] == "nws"
        report = {"status": it.get("status"), "reporter": it.get("source", "").partition("(")[2].rstrip(")"),
                  "at": it.get("reported"), "url": it.get("sourceUrl"), "note": it.get("note")} if nws else None
        if report:
            report["gov"] = nws_gov(report["reporter"])
        host = hosts.get(place(it)) if nws and place(it) else None
        if host is None:
            if nws:
                it["nws"] = [report]
            if place(it):
                hosts.setdefault(place(it), it)
            out.append(it)
            continue
        host.setdefault("nws", []).append(report)
        host["nws"].sort(key=lambda r: r["at"] or "", reverse=True)
        if E.SEVERITY.get(report["status"], 0) > E.SEVERITY.get(host.get("status"), 0):
            # the pin's own source still only supports its own status; statusBy is the NWS report that raised it
            host.setdefault("baseStatus", host.get("status"))
            host["status"], host["statusBy"] = report["status"], report
    out.sort(key=lambda it: it["id"])
    return out


def keep_independent(items: list[dict]) -> list[dict]:
    """Mike (Nov 3): a report its own feed dropped is shown faded as "Cleared by FHP/FL511". Only that feed's part fades:
    a pin that also carries independent evidence (a government page that raised or confirmed it, or an NWS storm report
    joined into it) stays active, showing what that evidence still says, and names it in keptBy (Codex #252)."""
    for it in items:
        if not it.get("noLongerListed"):
            continue
        page = it.get("raisedBy") or it.get("supportedBy") or ((it.get("verified") or {}).get("kind") == "page" and it["verified"])
        kept = []
        if page:  # the page's own words set or confirmed this impact; it stands without the feed
            kept.append({"kind": "page", "source": page.get("agency") or page.get("source"), "url": page.get("url")})
        nws = it.get("nws") or []
        if nws:
            kept += [{"kind": "nws", "source": "NWS storm report", "reporter": r.get("reporter"), "url": r.get("url"),
                      "status": r.get("status")} for r in nws]
            if not page:  # what remains is only the NWS reports: their most serious status, never the feed's full closure
                top = max(nws, key=lambda r: E.SEVERITY.get(r.get("status"), 0))
                it.setdefault("baseStatus", it.get("status"))
                it.update(status=top.get("status"), statusBy=top, fullClosure=False)
                # the dropped feed's lane and stretch wording no longer describes the road; kept only as its earlier report
                gone = {k: it.pop(k) for k in ("lanes", "line", "from", "to", "length_m") if k in it}
                if gone.get("lanes"):
                    it["earlierReport"] = f"{it['noLongerListed']['agency']} earlier: {gone['lanes']}"
        if kept:
            it["keptBy"] = kept
    return items


def main() -> int:
    county = json.load(open(os.path.join(E.HERE, "county.json")))
    obs = json.load(open(OBS))
    now = E.utcnow()
    state = E.derive(E.load(), now)
    clearance = county.get("clearance", {})
    fhp_clears = bool(clearance.get("enabled")) and "fhp-dropped" in clearance.get("kinds", [])
    new, notes = plan(obs, state, E.iso(now), county["id"], fhp_clears=fhp_clears)
    written = E.append(new, clearance_enabled=bool(clearance.get("enabled")), now=now)
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
