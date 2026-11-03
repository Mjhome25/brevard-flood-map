#!/usr/bin/env python3
"""Validate closure_events.jsonl before anything is committed or deployed. Exit 1 stops the run.

  python3 check_events.py --before <copy of the history at the start of the run> [--events closure_events.jsonl]

Checks: the history only grew (the earlier bytes are unchanged), every line parses and passes the shared schema,
seq runs 1, 2, 3 ..., eids are unique and match their content, cleared events obey the county's clearance setting,
and no record that was active before the run stopped being active without a cleared event.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also under python3 -I
import events as E  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True, help="the accepted history at the start of the run ('' if none)")
    p.add_argument("--events", default=E.EVENTS)
    p.add_argument("--county", default=os.path.join(E.HERE, "county.json"))
    a = p.parse_args(argv)
    enabled = bool(json.load(open(a.county)).get("clearance", {}).get("enabled"))
    before = open(a.before, "rb").read() if a.before and os.path.exists(a.before) else b""
    after = open(a.events, "rb").read() if os.path.exists(a.events) else b""
    problems = []
    if not after.startswith(before):
        problems.append("history was edited or shortened: the earlier lines are not an unchanged prefix")
    if after and not after.endswith(b"\n"):
        problems.append("history does not end with a newline")
    try:
        evs = E.load(a.events)
    except (E.Refused, json.JSONDecodeError) as exc:
        evs, problems = [], problems + [f"unreadable history: {exc}"]
    seq, eids = 0, set()
    for ev in evs:
        try:
            E.validate(ev, seq, enabled)
        except E.Refused as exc:
            problems.append(str(exc))
        if ev.get("eid") in eids:
            problems.append(f"seq {ev.get('seq')}: duplicate eid")
        eids.add(ev.get("eid"))
        seq = ev.get("seq") if isinstance(ev.get("seq"), int) else seq + 1
    if not problems and before:
        n_before = before.count(b"\n")
        was = E.derive(evs[:n_before])["records"]
        now = E.derive(evs)["records"]
        for rid, r in was.items():
            if r["status"] == "active" and (rid not in now or now[rid]["status"] not in ("active", "reopened")):
                problems.append(f"{rid} was active and is no longer, without a cleared event")
    for msg in problems:
        print("check_events:", msg, file=sys.stderr)
    print(f"check_events: {len(evs)} events, {'FAILED' if problems else 'ok'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
