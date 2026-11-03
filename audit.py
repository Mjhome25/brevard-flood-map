#!/usr/bin/env python3
"""Daily audit of everything taken off the active map, from any source (Mike, Nov 3 2026: "make the daily audit of
ANYTHING that we take off the list, to make sure no website is giving us bad data"; no alerts, a quiet ledger).

Replays closure_events.jsonl and finds every report that actually left the active list (an FHP crash dropped, an
official reopening) since the last audit, plus every boil-water notice rescission in water_notices.json. Each gets a
verdict from our own later data:
  conflict           - the place was still reported blocked after the removal: the same source listed it again, or
                       another source's report at the same place was active then or newer
  corroborated-open  - an official statement was the reason, and nothing contradicts it
  unknown            - nothing else reports that place either way (never read as open)
It writes audit/YYYY-MM-DD.json (one row per removal, road-only) and one line in audit/ledger.md. It never writes
closure_events.jsonl, clears, lowers or hides anything; a conflict is a candidate for a reviewed restore.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import events as E  # noqa: E402
import reconcile as R  # noqa: E402

AUDIT = os.path.join(HERE, "audit")
END = "2026-12-03"  # Mike: one month
DUE_HOUR = 12  # UTC: the first run at or after 7 AM Eastern


def removals(events: list[dict]) -> list[dict]:
    """Every cleared event that took an active report off the map, with the report as it stood."""
    state, out = E._empty(), []
    for ev in events:
        before = state["records"].get(ev.get("rid"))
        was = before["status"] if before else None
        fields = dict(before["fields"]) if before else {}
        E.apply(state, ev)
        r = state["records"].get(ev.get("rid"))
        if ev["kind"] == "cleared" and was == "active" and r and r["status"] == "reopened":
            out.append({"rid": ev["rid"], "src": ev["src"], "at": ev["at"], "reopenedAt": r["reopenedAt"],
                        "why": ev["evidence"].get("kind"), "fields": fields, "evidence": ev["evidence"]})
    return out


def verdict(rm: dict, events: list[dict], final: dict) -> tuple[str, list[str]]:
    seen = []
    for ev in events:
        if ev["at"] > rm["at"] and ev.get("rid") == rm["rid"] and ev["kind"] in ("reported", "relisted"):
            st = E.parse(ev.get("stime"))
            if rm["why"] == "fhp-dropped" or (st and st > E.parse(rm["reopenedAt"])):
                seen.append(f"{rm['src']} listed it again at {ev['at']}")
                break
    for rid, r in final["records"].items():
        if rid == rm["rid"] or not R.same_spot(rm["fields"], r["fields"]):
            continue
        cut = E.parse(rm["reopenedAt"])
        if E.parse(r["lastReported"]) > cut or (r["status"] == "active" and E.parse(r["firstReported"]) <= cut):
            seen.append(f"{r['src']} report {rid} at the same place (last reported {r['lastReported']})")
    if seen:
        return "conflict", seen
    return ("corroborated-open" if rm["why"] == "official" else "unknown"), []


def water_rescissions(path: str) -> list[dict]:
    try:
        data = json.load(open(path))
    except (OSError, ValueError):
        return []
    hist = data.get("history") or [] if isinstance(data, dict) else []
    out = []
    for i, h in enumerate(hist):
        if not isinstance(h, dict) or h.get("to") != "rescinded":
            continue
        again = [x for x in hist[i + 1:] if isinstance(x, dict) and x.get("id") == h.get("id") and x.get("to") == "active"]
        out.append({"rid": "water:" + str(h.get("id")), "src": "water", "at": h.get("detectedAt") or "", "why": "official",
                    "where": "Melbourne water service area", "source": h.get("sourceUrl"),
                    "verdict": "conflict" if again else "corroborated-open",
                    "seen": [f"notice active again ({x.get('sourceUrl')}, {x.get('publishedDate')})" for x in again]})
    return out


def run(now: datetime.datetime, since: str, events_path: str = E.EVENTS,
        water_path: str = os.path.join(HERE, "water_notices.json")) -> dict:
    events = E.load(events_path)
    final = E.derive(events, now)
    rows = []
    for rm in removals(events):
        if rm["at"] <= since:
            continue
        v, seen = verdict(rm, events, final)
        f = rm["fields"]
        rows.append({"rid": rm["rid"], "src": rm["src"], "removedAt": rm["at"], "why": rm["why"],
                     "where": f.get("where") or f.get("road") or "", "status": f.get("status"),
                     "source": rm["evidence"].get("url"), "verdict": v, "seen": seen})
    rows += [w for w in water_rescissions(water_path) if w["at"] > since]
    down = sorted(s for s, h in final["sources"].items() if h.get("state") != "up")
    count = lambda v: sum(1 for r in rows if r["verdict"] == v)
    return {"date": now.strftime("%Y-%m-%d"), "since": since, "through": E.iso(now), "removed": len(rows),
            "conflict": count("conflict"), "corroboratedOpen": count("corroborated-open"), "unknown": count("unknown"),
            "sourcesNotUp": down, "rows": rows}


def main(argv: list[str]) -> int:
    now = E.utcnow()
    os.makedirs(AUDIT, exist_ok=True)
    last_path = os.path.join(AUDIT, "last.json")
    try:
        last = json.load(open(last_path))
    except (OSError, ValueError):
        last = {}
    today = now.strftime("%Y-%m-%d")
    if "--due" in argv and (today > END or now.hour < DUE_HOUR or last.get("date") == today):
        print("audit: not due")
        return 0
    rep = run(now, last.get("through") or "")
    with open(os.path.join(AUDIT, f"{today}.json"), "w") as f:
        json.dump(rep, f, indent=1)
        f.write("\n")
    with open(os.path.join(AUDIT, "ledger.md"), "a") as f:
        if f.tell() == 0:
            f.write("# Removal audit ledger (one line per day; details in audit/YYYY-MM-DD.json)\n\n")
        f.write(f"- {today}: removed {rep['removed']}, conflict {rep['conflict']}, corroborated-open "
                f"{rep['corroboratedOpen']}, unknown {rep['unknown']}, sources not up: "
                f"{', '.join(rep['sourcesNotUp']) or 'none'}\n")
    with open(last_path, "w") as f:
        json.dump({"date": today, "through": rep["through"]}, f)
        f.write("\n")
    print(f"audit {today}: removed {rep['removed']}, conflict {rep['conflict']}, unknown {rep['unknown']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
