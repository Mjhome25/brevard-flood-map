#!/usr/bin/env python3
"""Daily removal ledger (Mike, Nov 3 2026: "make the daily audit of ANYTHING that we take off the list, to make sure
no website is giving us bad data"; no alerts, a quiet ledger). Read-only: it never writes closure_events.jsonl and never
clears, lowers or hides anything; a case it flags is a candidate for a reviewed restore.

Cases, rebuilt from the whole history and re-judged every day (evidence that arrives tomorrow can still flag today's):
  removed     - a report that left the active map (an FHP crash dropped, an official reopening)
  disappeared - a source stopped listing a report we kept on the map (e.g. an FHP road closure); the sample that shows
                whether a source dropping something means the road is open
  water       - a boil-water notice rescission (water_notices.json history)
Verdicts, from our own later data at the same place, same direction, with at least the same impact:
  still-blocked     - a report that began before the drop was reported again after it
  later-report      - a report that began after the drop (a recurrence or proof; needs a human look)
  source-relisted   - the same source listed the same incident again after dropping it
  stale-unresolved  - another report was active at the drop but has not been reported again since
  corroborated-open - an official statement was the reason, and nothing above applies
  unknown           - nothing else either way (never read as open)
This is the removal ledger only. Coverage (official advisory pages vs our pins) is a separate step, not yet built.
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


def cases(events: list[dict]) -> list[dict]:
    """Every removal and every kept disappearance, with the report as it stood at that moment."""
    state, out = E._empty(), []
    for ev in events:
        before = state["records"].get(ev.get("rid"))
        was = dict(before) if before else None
        E.apply(state, ev)
        r = state["records"].get(ev.get("rid"))
        if not was or not r or was["status"] != "active":
            continue
        base = {"rid": ev["rid"], "src": ev["src"], "at": ev["at"], "fields": dict(was["fields"]),
                "lastReported": was["lastReported"], "firstReported": was["firstReported"]}
        if ev["kind"] == "cleared" and r["status"] == "reopened":
            out.append(dict(base, id="removed:" + ev["eid"], kind="removed", why=ev["evidence"].get("kind"),
                            cut=r["reopenedAt"], source=ev["evidence"].get("url")))
        elif ev["kind"] == "missing" and not was["missingSince"] and r["missingSince"]:
            out.append(dict(base, id="disappeared:" + ev["eid"], kind="disappeared", why="no longer listed",
                            cut=ev["at"], source=was["fields"].get("sourceUrl")))
    return out


def verdict(c: dict, events: list[dict], final: dict) -> tuple[str, list[str]]:
    cut = E.parse(c["cut"])
    later = [ev for ev in events if ev.get("rid") == c["rid"] and ev["kind"] in ("reported", "relisted")
             and ev["at"] > c["at"]]
    if later and (c["kind"] == "disappeared" or c["why"] == "fhp-dropped"):
        return "source-relisted", [f"{c['src']} listed it again at {later[0]['at']} (source time {later[0].get('stime')})"]
    found = {"still-blocked": [], "later-report": [], "stale-unresolved": []}
    for rid, r in final["records"].items():
        if rid == c["rid"] or E.rank(r["fields"]) < E.rank(c["fields"]) or not R.same_spot(c["fields"], r["fields"]):
            continue
        first, last = E.parse(r["firstReported"]), E.parse(r["lastReported"])
        note = f"{r['src']} {rid}: {r['fields'].get('status')}, first {r['firstReported']}, last {r['lastReported']}"
        if first > cut:
            found["later-report"].append(note)
        elif last > cut:
            found["still-blocked"].append(note)
        elif r["status"] == "active":
            found["stale-unresolved"].append(note)
    for v in ("still-blocked", "later-report", "stale-unresolved"):
        if found[v]:
            return v, found[v]
    return ("corroborated-open" if c.get("why") == "official" else "unknown"), []


def water_cases(path: str) -> tuple[list[dict], bool]:
    """(rescission cases, readable)."""
    try:
        data = json.load(open(path))
        hist = data.get("history") or []
        if not isinstance(hist, list):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        return [], False
    out = []
    for i, h in enumerate(hist):
        if not isinstance(h, dict) or h.get("to") != "rescinded":
            continue
        again = [x for x in hist[i + 1:] if isinstance(x, dict) and x.get("id") == h.get("id") and x.get("to") == "active"]
        out.append({"id": f"water:{h.get('id')}:{h.get('detectedAt')}", "kind": "water", "rid": "water:" + str(h.get("id")),
                    "src": "water", "at": h.get("detectedAt") or "", "why": "official", "where": "Melbourne water service area",
                    "status": "boil water notice", "source": h.get("sourceUrl"),
                    "verdict": "source-relisted" if again else "corroborated-open",
                    "seen": [f"notice active again ({x.get('sourceUrl')}, {x.get('publishedDate')})" for x in again]})
    return out, True


def run(now: datetime.datetime, events_path: str = E.EVENTS, water_path: str = os.path.join(HERE, "water_notices.json"),
        known: dict | None = None) -> dict:
    """The full report for today; `known` is yesterday's case book (first-checked dates and verdict history)."""
    known = known or {}
    today = now.strftime("%Y-%m-%d")
    events = E.load(events_path)
    final = E.derive(events, now)
    rows = []
    for c in cases(events):
        v, seen = verdict(c, events, final)
        f = c["fields"]
        rows.append({"id": c["id"], "kind": c["kind"], "rid": c["rid"], "src": c["src"], "at": c["at"], "why": c["why"],
                     "where": f.get("where") or f.get("road") or "", "status": f.get("status"),
                     "fullClosure": bool(f.get("fullClosure")), "lastReported": c["lastReported"],
                     "source": c["source"], "verdict": v, "seen": seen})
    water, water_ok = water_cases(water_path)
    rows += water
    book = {}
    for r in rows:
        old = known.get(r["id"]) or {}
        hist = list(old.get("verdicts") or [])
        if not hist or hist[-1]["verdict"] != r["verdict"]:
            hist.append({"date": today, "verdict": r["verdict"]})
        r["firstChecked"], r["lastChecked"] = old.get("firstChecked") or today, today
        r["new"], r["changed"] = r["id"] not in known, bool(old) and old.get("verdicts", [{}])[-1].get("verdict") != r["verdict"]
        book[r["id"]] = {"firstChecked": r["firstChecked"], "lastChecked": today, "verdicts": hist}
    tally = lambda rs: {v: sum(1 for r in rs if r["verdict"] == v) for v in sorted({r["verdict"] for r in rs})}
    by_kind = {k: tally([r for r in rows if r["kind"] == k]) for k in ("removed", "disappeared", "water")}
    down = sorted(s for s, h in final["sources"].items() if h.get("state") != "up")
    return {"date": today, "through": E.iso(now), "scope": "removal ledger only; coverage of official pages is not part "
            "of this audit", "complete": water_ok, "unavailable": [] if water_ok else ["water_notices.json"],
            "sourcesNotUp": down, "cases": len(rows), "new": sum(r["new"] for r in rows),
            "changed": sum(r["changed"] for r in rows), "byKind": by_kind, "rows": rows, "book": book}


def main(argv: list[str]) -> int:
    now = E.utcnow()
    today = now.strftime("%Y-%m-%d")
    os.makedirs(AUDIT, exist_ok=True)
    last_path, book_path = os.path.join(AUDIT, "last.json"), os.path.join(AUDIT, "cases.json")
    try:
        last = json.load(open(last_path))
    except (OSError, ValueError):
        last = {}
    if "--due" in argv and (today > END or now.hour < DUE_HOUR or last.get("date") == today):
        print("audit: not due")
        return 0
    try:
        try:
            known = json.load(open(book_path))
        except (OSError, ValueError):
            known = {}
        rep = run(now, known=known)
        book = rep.pop("book")
        write = lambda p, obj: (open(p + ".tmp", "w").write(json.dumps(obj, indent=1) + "\n"), os.replace(p + ".tmp", p))
        write(os.path.join(AUDIT, f"{today}.json"), rep)
        write(book_path, book)
        kinds = "; ".join(f"{k} {sum(v.values())} ({', '.join(f'{n} {x}' for x, n in v.items()) or 'none'})"
                          for k, v in rep["byKind"].items())
        line = (f"- {today}: {rep['cases']} cases, {rep['new']} new, {rep['changed']} changed verdict. {kinds}. "
                f"Sources not up: {', '.join(rep['sourcesNotUp']) or 'none'}. "
                f"Unavailable: {', '.join(rep['unavailable']) or 'none'}.\n")
    except Exception as exc:  # a failed audit leaves a durable record and never stops the map
        line = f"- {today}: AUDIT FAILED ({type(exc).__name__}); no report written, will retry next run.\n"
        rep = None
    if rep is not None or last.get("failed") != today:  # one failure line a day, retried every run
        with open(os.path.join(AUDIT, "ledger.md"), "a") as f:
            if f.tell() == 0:
                f.write("# Removal ledger (one line per day; cases in audit/YYYY-MM-DD.json). Coverage is a separate step.\n\n")
            f.write(line)
    with open(last_path, "w") as f:
        json.dump({"date": today, "through": rep["through"]} if rep is not None else dict(last, failed=today), f)
        f.write("\n")
    print("audit:", line.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
