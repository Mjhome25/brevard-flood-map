#!/usr/bin/env python3
"""FHP/FL511 drop trial (Mike, Nov 3 2026, 17:36Z: "we will institute dropped reports if we go 1 week and don't find any
evidence that a cleared FHP report or FL511 report is still an active closed road or problem"; 17:37Z: "any time FHP or
FL511 takes anything off, we have to make sure there is no other reported closures that confirm that being wrong. any
you find, bring them to me (they may just be other stale entries from sites not taken off yet)").

Read-only, every run: writes audit/drops.json. It never writes the closure history and never clears anything.

An episode is one observed disappearance: a live feed (FHP or FL511) stopped listing a report it had listed (a missing
event). The crash retirement that may follow is the same episode; an official reopening is not a drop. The trial cohort
is the episodes that began in the seven days from Mike's decision; earlier ones are a baseline, listed apart.

For each episode, every other report on the map at the same place (same intersection or mile marker, not the opposite
direction) is a counterpart, with its own citation and state:
  source-relisted     - the same feed listed the same incident again after the drop
  reported-after-drop - another report that began before the drop and was reported again after it (possible persistence)
  later-report        - another report that began after the drop (a recurrence, or proof the road was not clear)
  older-entry         - another report last reported before the drop and still on the map (possibly stale)
A counterpart counts when it shows at least the dropped report's impact on the public page (closed = status closed or
a full closure, whatever its category); lesser ones are listed apart. An episode with no counterpart is "nothing found":
that is the absence of contrary evidence, never proof the road reopened.

Findings Mike has not reviewed are flagged new or changed when they first appear or change, so they are brought to him
in chat once (the only notice he authorised), not repeated every run. His decisions go in audit/drop_review.json
({episode id: {"decision": ..., "at": ..., "note": ...}}).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import events as E  # noqa: E402
import reconcile as R  # noqa: E402

AUDIT = os.path.join(HERE, "audit")
FEEDS = ("fhp", "fl511")
START = "2026-11-03T17:36:43Z"  # Mike's decision message
END = "2026-11-10T17:36:43Z"  # seven days
STATE_ROAD = re.compile(r"\b(?:I-?\s?\d+|US-?\s?\d+|U\.S\.\s?\d+|SR-?\s?\d+|State Road \d+|A1A|Beachline|Turnpike)\b", re.I)


def level(f: dict) -> int:
    """Impact as the public page shows it: 2 closed (status closed or a full closure), 1 flooding or a hazard, 0 caution."""
    if f.get("status") == "closed" or f.get("fullClosure"):
        return 2
    return min(E.SEVERITY.get(f.get("status"), 1), 1)


def group(f: dict) -> str:
    """crash or flooding/hazard, on a state road or a local street (Mike's question: does a drop mean the same for each)."""
    kind = "crash" if re.search(r"\bMVC|crash|collision\b", f.get("title") or "", re.I) else "flooding/hazard"
    road = f.get("road") or f.get("where") or ""
    return f"{kind}, {'state road' if STATE_ROAD.search(road) else 'local street'}"


def episodes(events: list[dict]) -> list[dict]:
    """Every observed feed disappearance, with the report as it stood when it disappeared."""
    state, out = E._empty(), []
    for ev in events:
        before = state["records"].get(ev.get("rid"))
        was = dict(before) if before else None
        E.apply(state, ev)
        r = state["records"].get(ev.get("rid"))
        if (ev["kind"] == "missing" and ev.get("src") in FEEDS and was and r and was["status"] == "active"
                and not was["missingSince"] and r["missingSince"]):
            out.append({"id": "drop:" + ev["eid"], "rid": ev["rid"], "src": ev["src"], "missingSince": r["missingSince"],
                        "seenGoneAt": ev["at"], "lastListed": was["lastReported"], "firstReported": was["firstReported"],
                        "fields": dict(was["fields"])})
    return out


def counterparts(ep: dict, events: list[dict], final: dict) -> tuple[list[dict], list[dict]]:
    cut = E.parse(ep["missingSince"])
    found, lesser = [], []
    again = [ev for ev in events if ev.get("rid") == ep["rid"] and ev["kind"] in ("reported", "relisted")
             and E.parse(ev["at"]) > cut]
    if again:
        f = again[0].get("fields") or ep["fields"]
        found.append({"relation": "source-relisted", "rid": ep["rid"], "src": ep["src"], "source": f.get("source"),
                      "url": f.get("sourceUrl"), "status": f.get("status"), "fullClosure": bool(f.get("fullClosure")),
                      "reportedAt": again[0]["at"], "sourceTime": again[0].get("stime")})
    for rid, r in final["records"].items():
        if rid == ep["rid"] or r["status"] != "active" and E.parse(r["lastReported"]) <= cut:
            continue
        if not R.same_spot(ep["fields"], r["fields"]):
            continue
        first, last = E.parse(r["firstReported"]), E.parse(r["lastReported"])
        rel = ("later-report" if first > cut else "reported-after-drop" if last > cut
               else "older-entry" if r["status"] == "active" else None)
        if not rel:
            continue
        f = r["fields"]
        listing = ("no longer listed by its feed" if r["src"] in FEEDS and r.get("missingSince")
                   else "still listed by its feed" if r["src"] in FEEDS
                   else "not a live feed (cannot tell if its source still lists it)")
        row = {"relation": rel, "rid": rid, "src": r["src"], "source": f.get("source"), "url": f.get("sourceUrl"),
               "where": f.get("where"), "status": f.get("status"), "fullClosure": bool(f.get("fullClosure")),
               "firstReported": r["firstReported"], "lastReported": r["lastReported"],
               "missingSince": r.get("missingSince"), "onMap": r["status"] == "active", "listing": listing}
        (found if level(f) >= level(ep["fields"]) else lesser).append(row)
    return found, lesser


def report(now: datetime.datetime, events_path: str = E.EVENTS, prev: dict | None = None,
           review: dict | None = None) -> dict:
    prev, review = prev or {}, review or {}
    events = E.load(events_path)
    final = E.derive(events, now)
    old = {c["id"]: c for c in prev.get("cases", [])}
    cases = []
    for ep in episodes(events):
        found, lesser = counterparts(ep, events, final)
        f = ep["fields"]
        key = sorted((c["relation"], c["rid"]) for c in found)
        before = old.get(ep["id"])
        c = {"id": ep["id"], "rid": ep["rid"], "src": ep["src"], "where": f.get("where") or f.get("road") or "",
             "status": f.get("status"), "fullClosure": bool(f.get("fullClosure")), "group": group(f),
             "url": f.get("sourceUrl"), "firstReported": ep["firstReported"], "lastListed": ep["lastListed"],
             "missingSince": ep["missingSince"], "seenGoneAt": ep["seenGoneAt"],
             "cohort": "trial" if START <= ep["missingSince"] < END else "baseline" if ep["missingSince"] < START else "after",
             "finding": ("contrary evidence" if any(x["relation"] in ("source-relisted", "reported-after-drop", "later-report")
                                                    for x in found) else "possibly stale entry" if found else "nothing found"),
             "counterparts": found, "lesser": lesser, "review": review.get(ep["id"], {}).get("decision", "unreviewed"),
             "firstFound": (before or {}).get("firstFound") or (E.iso(now) if found else None), "checkedAt": E.iso(now)}
        c["new"] = bool(found) and not before
        c["changed"] = bool(before) and sorted((x["relation"], x["rid"]) for x in before.get("counterparts", [])) != key
        cases.append(c)
    trial = [c for c in cases if c["cohort"] == "trial"]
    tally = lambda cs: {"episodes": len(cs), "incidents": len({c["rid"] for c in cs}),
                        "bySource": {s: sum(c["src"] == s for c in cs) for s in FEEDS},
                        **{k: sum(c["finding"] == k for c in cs) for k in ("contrary evidence", "possibly stale entry",
                                                                             "nothing found")}}
    groups = sorted({c["group"] for c in trial})
    return {"checkedAt": E.iso(now), "trial": {"from": START, "to": END, "complete": E.iso(now) >= END, **tally(trial),
                                                "byGroup": {g: tally([c for c in trial if c["group"] == g]) for g in groups}},
            "baseline": tally([c for c in cases if c["cohort"] == "baseline"]),
            "sourcesNotUp": sorted(s for s, h in final["sources"].items() if s in FEEDS and h.get("state") != "up"),
            "forMike": [c["id"] for c in cases if c["counterparts"] and c["review"] == "unreviewed"],
            "newForMike": [c["id"] for c in cases if (c["new"] or c["changed"]) and c["review"] == "unreviewed"],
            "note": "nothing found means no contrary report on our map, not proof the road reopened",
            "cases": cases}


def line(rep: dict) -> str:
    t, b = rep["trial"], rep["baseline"]
    return (f"FHP/FL511 drop trial ({t['from'][:10]} to {t['to'][:10]}): {t['episodes']} drops "
            f"({t['incidents']} incidents), {t['contrary evidence']} with contrary evidence, {t['possibly stale entry']} "
            f"with a possibly stale entry, {t['nothing found']} nothing found (not proof). Before the trial: "
            f"{b['episodes']} drops, {b['contrary evidence']} contrary, {b['possibly stale entry']} possibly stale."
            + (f" Feeds not up: {', '.join(rep['sourcesNotUp'])}." if rep["sourcesNotUp"] else ""))


def main() -> int:
    path = os.path.join(AUDIT, "drops.json")
    load = lambda p: json.load(open(p)) if os.path.exists(p) else {}
    try:
        rep = report(E.utcnow(), prev=load(path), review=load(os.path.join(AUDIT, "drop_review.json")))
    except Exception as exc:  # never stops the map; the last good report stays
        print(f"drops: failed ({type(exc).__name__}); last report kept")
        return 0
    os.makedirs(AUDIT, exist_ok=True)
    open(path + ".tmp", "w").write(json.dumps(rep, indent=1) + "\n")
    os.replace(path + ".tmp", path)
    print("drops:", line(rep), f"New for Mike: {len(rep['newForMike'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
