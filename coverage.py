#!/usr/bin/env python3
"""Official advisory coverage check (Mike, Nov 3 2026: "how do we fix this and catch these closures").

Each run reads the newest same-day traffic updates on official city advisory pages and accounts for every line:
  closure lines - covered (an active pin at that place, shown closed), weaker (a pin there, not shown closed: a raise
                  candidate) or missed (no pin: an add candidate)
  open / clear  - the active pins on that road, as reopening candidates for a human or verify.py --open
  dropped       - a closure in an earlier same-day update that the newest update no longer lists (by place, not wording)
  earlierOpen   - open / clear statements in earlier same-day updates, still candidates
Writes audit/coverage.json. Read-only: it never writes closure_events.jsonl or changes a pin; a source that cannot be
read is listed as unavailable, never as zero closures.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import events as E  # noqa: E402
import verify as V  # noqa: E402

OUT = os.path.join(HERE, "audit", "coverage.json")
SOURCES = [{"agency": "City of Melbourne", "host": "www.melbourneflorida.org",
            "index": "https://www.melbourneflorida.org/Government/City-News/Traffic-Advisories"}]
POSTS = 4  # newest posts read per source
DIRS = r"(?:north|south|east|west)bound"
GENERIC = {"street", "road", "boulevard", "avenue", "drive", "parkway", "lane", "highway", "court", "place", "causeway",
           "north", "south", "east", "west", "n", "s", "e", "w", "nb", "sb", "eb", "wb", "the", "of", "all"}


def base(name: str) -> str:
    """'Sun Lakes Rd' and 'Sunlake Road' -> 'sunlake'; 'East Ave B' -> 'b'... kept as the road's own words."""
    words = [w for w in E.road_words(name).split() if w not in GENERIC]
    s = "".join(words)
    return s[:-1] if len(s) > 4 and s.endswith("s") else s


def names(fields: dict) -> set[str]:
    raw = " / ".join(str(fields.get(k) or "") for k in ("road", "roadShort", "where", "spot"))
    raw = re.sub(r"\[[^\]]*\]|\([^)]*\)|\bat their intersection\b|\ball directions\b|\b(?:NB|SB|EB|WB)\b", " ", raw)
    parts = re.split(r"\s*(?:/|,|\bat\b|\band\b|\bbetween\b|\bnear\b|\bto\b)\s*", raw, flags=re.I)
    return {b for b in (base(p) for p in parts) if b}


CLOSURE = [re.compile(rf"^(?P<a>[A-Z][\w .']+?)\s*/\s*(?P<b>[A-Z][\w .']+?)\s*[-–]\s*(?P<rest>.*\bclosed\b.*)$"),
           re.compile(rf"^(?P<a>[A-Z][\w .']+?)\s*[-–]\s*between (?P<b>[\w .']+?) and (?P<c>[\w .']+?) (?P<rest>.*\bclosed\b.*)$"),
           re.compile(rf"^(?:All of )?(?P<a>[A-Z][\w .']+?) (?:at (?P<b>[A-Z][\w .']+?) )?(?:is|remains) (?P<rest>closed.*)$")]
OPENING = re.compile(rf"^(?P<a>[A-Z][\w .']+?) (?:is|has) (?:now )?(?:clear|open|reopened)"
                     rf"(?: from (?P<b>[\w .']+?) to (?P<c>[\w .']+?))?(?=[,.\s]|$)")


def classify(line: str) -> dict | None:
    for rx in CLOSURE:
        m = rx.match(line)
        if m:
            d = m.groupdict()
            dirs = re.findall(DIRS, d.get("rest") or "", re.I)
            if rx is CLOSURE[1]:
                return {"kind": "closure", "road": base(d["a"]), "cross": [base(d["b"]), base(d["c"])], "dirs": dirs}
            return {"kind": "closure", "road": base(d["a"]), "cross": [base(d["b"])] if d.get("b") else [], "dirs": dirs}
    m = OPENING.match(line)
    if m:
        return {"kind": "open", "road": base(m["a"]), "cross": [base(x) for x in (m["b"], m["c"]) if x]}
    return None


def account(line: str, c: dict, active: dict) -> dict:
    hits = []
    for rid, r in active.items():
        n = names(r["fields"])
        if c["road"] not in n:
            continue
        if c["kind"] == "closure" and c["cross"] and not any(x in n for x in c["cross"]):
            continue
        hits.append(rid)
    row = {"line": line, "kind": c["kind"], "pins": hits}
    if c["kind"] == "closure":
        closed = [rid for rid in hits if active[rid]["fields"].get("status") == "closed"]
        row["verdict"] = "covered" if closed else "weaker" if hits else "missed"
    else:
        row["verdict"] = "reopen-candidates" if hits else "no-active-pin"
    return row


def fetch(url: str, host: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname != host or p.port or p.username:
        raise ValueError("unexpected source")
    req = urllib.request.Request(url, headers={"User-Agent": V.UA})
    with urllib.request.build_opener(V.SameHost()).open(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def post_lines(page: str) -> tuple[str | None, str, list[str]]:
    """(published date, update heading, the post's lines after it)."""
    parser = V.Posts()
    parser.feed(re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I))
    flat = lambda parts: re.sub(r"\s+", " ", " ".join(parts).replace("\xa0", " ")).strip()
    units = [flat(u) for _, u in parser.units if flat(u)]
    date = None
    for u in units:
        m = V.PUBLISHED.search(u)
        if m:
            date = datetime.datetime.strptime(" ".join(m.groups()), "%B %d %Y").strftime("%Y-%m-%d")
            break
    head = next((u for u in units if V.HEADING.fullmatch(u)), "")
    return date, head, units


def run(now: datetime.datetime, get=fetch, events_path: str = E.EVENTS) -> dict:
    today = now.astimezone(E.ET).strftime("%Y-%m-%d")
    active = {k: r for k, r in E.derive(E.load(events_path), now)["records"].items() if r["status"] == "active"}
    out = {"checkedAt": E.iso(now), "sources": [], "unavailable": []}
    for src in SOURCES:
        try:
            index = get(src["index"], src["host"])
        except Exception as exc:
            out["unavailable"].append({"agency": src["agency"], "url": src["index"], "error": type(exc).__name__})
            continue
        urls = []
        for href in re.findall(r'href="([^"]+)"', index):
            u = urllib.parse.urljoin(src["index"] + "/", href)
            if u.startswith(src["index"] + "/") and u not in urls:
                urls.append(u)
        posts = []
        for u in urls[:POSTS]:
            try:
                date, head, units = post_lines(get(u, src["host"]))
            except Exception as exc:
                out["unavailable"].append({"agency": src["agency"], "url": u, "error": type(exc).__name__})
                continue
            if date == today:
                posts.append({"url": u, "heading": head, "units": units,
                              "time": V.stated_time(units, units[-1], date) if units else ""})
        posts.sort(key=lambda p: p["time"] or "", reverse=True)
        rep = {"agency": src["agency"], "index": src["index"], "posts": [p["url"] for p in posts], "lines": [],
               "dropped": [], "earlierOpen": []}
        place = lambda c: (c["kind"], frozenset([c["road"], *c["cross"]]))
        if posts:
            newest = posts[0]
            rep["newest"] = {"url": newest["url"], "heading": newest["heading"], "time": newest["time"]}
            seen = set()
            for u in newest["units"]:
                c = classify(u)
                if c:
                    rep["lines"].append(account(u, c, active))
                    seen.add(place(c))
            for p in posts[1:]:  # newest first, so each place is reported once, from its latest mention
                for u in p["units"]:
                    c = classify(u)
                    if not c or place(c) in seen:
                        continue
                    seen.add(place(c))
                    row = dict(account(u, c, active), url=p["url"], heading=p["heading"])
                    (rep["dropped"] if c["kind"] == "closure" else rep["earlierOpen"]).append(row)
        count = lambda v: sum(1 for x in rep["lines"] if x["verdict"] == v)
        rep["summary"] = {v: count(v) for v in ("covered", "weaker", "missed", "reopen-candidates")}
        rep["summary"]["dropped"] = len(rep["dropped"])
        out["sources"].append(rep)
    return out


def main() -> int:
    try:
        rep = run(E.utcnow())
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT + ".tmp", "w") as f:
            json.dump(rep, f, indent=1)
            f.write("\n")
        os.replace(OUT + ".tmp", OUT)
        for s in rep["sources"]:
            print("coverage:", s["agency"], s["summary"])
        if rep["unavailable"]:
            print("coverage: unavailable", [u["url"] for u in rep["unavailable"]])
    except Exception as exc:  # never stops the map
        print("coverage failed:", type(exc).__name__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
