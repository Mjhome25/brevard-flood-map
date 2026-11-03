#!/usr/bin/env python3
"""Official advisory coverage (Mike, Nov 3 2026: "how do we fix this and catch these closures"). Melbourne pilot.

Every run it reads the City of Melbourne traffic advisories, keeps an inventory of every road claim in every post of the
emergency episode (closed / open, with place, scope and direction), and compares the latest claim for each place with
our active pins:
  covered          - a pin with the same place, scope and direction (or no direction) that the page shows closed
  covered-road     - a road named alone in a closed list (extent not stated) has a closed pin on it
  partial          - pins on that road, but not the same scope (an endpoint for a stretch, a spot for "all of")
  direction        - a pin at that place for another direction only
  weaker           - a pin with that scope that the page does not show closed (a raise candidate)
  missed           - no pin (an add candidate)
  reopen-review    - the latest claim says open / clear; the pins on that road are listed for a human, never reopened
  direction-unstated / unlocated - a matching pin that states no direction for a directional claim / is not on the map
  resolved-by-city / resolved-official - a later whole-road open statement (from the city, or accepted in our history)
Sentences it cannot read strictly are counted as needs-review with a hash only: no unvetted prose is published.
A source it cannot read, whose layout it does not recognise, or that has no supported sentences is recorded as such,
never as zero closures. County-wide sources other than Melbourne are listed as pending.
Read-only: it never writes closure_events.jsonl or changes a pin. Writes audit/coverage.json.
"""
from __future__ import annotations

import datetime
import hashlib
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
EPISODE_START = "2026-11-02"
SOURCES = [{"agency": "City of Melbourne", "host": "www.melbourneflorida.org",
            "index": "https://www.melbourneflorida.org/Government/City-News/Traffic-Advisories"}]
PENDING = ["Space Coast Daily City of Melbourne closure list (14 closed, 8 flooded)", "WESH 2 closed-roads list",
           "Palm Bay", "West Melbourne", "Brevard County", "Cocoa", "Rockledge", "Titusville"]
DISCOVER = 15  # index links considered per run; more marks the inventory partial
REFETCH = 3  # newest posts re-read every run
RECHECK_HOURS = 12  # older episode posts are re-read at least this often (the city can correct an old page in place)
RECHECK_PER_RUN = 3
STALE_MIN = 60
ALIASES = {"sunlake road": "sun lakes road"}  # reviewed spellings of one road
SUFFIX_ONLY = {"street", "road", "boulevard", "avenue", "drive", "parkway", "lane", "highway", "court", "place", "causeway"}
DIRS = {"north": "NB", "south": "SB", "east": "EB", "west": "WB"}

# A road name is capitalised words (a lower-case suffix word allowed: "Palm Bay road"), so a verb phrase such as
# "Eber Road is no longer" can never be read as a name.
_W = r"[A-Z0-9][A-Za-z0-9.'-]*"
_N = rf"{_W}(?: (?:{_W}|of|the|road|street|avenue|boulevard|drive))*"
_NL = rf"{_W},?(?: (?:{_W},?|of|the))*"  # a list item may name "Dr. Martin Luther King, Jr. Boulevard"
_D = r"(?:(?:north|south|east|west)bound)"
CLOSED_TAIL = r"(?:remains|is) closed(?: with barricades blocking the roadway)?"
SENTENCES = [  # every sentence must match one of these exactly; anything else is needs-review
    ("closure", "point", re.compile(rf"^(?P<a>{_N})\s*/\s*(?P<b>{_N})\s*-\s*(?:(?P<dirs>{_D}(?: and {_D})?) )?{CLOSED_TAIL}\.?$")),
    ("closure", "segment", re.compile(rf"^(?P<a>{_N})\s*-\s*between (?P<x>{_N}) and (?P<y>{_N}) {CLOSED_TAIL}\.?$")),
    ("closure", "point", re.compile(rf"^(?P<a>{_N}) at (?P<b>{_N}) is closed\.?$")),
    ("closure", "point", re.compile(rf"^(?P<a>{_N}) \((?P<dirs>{_D})\) is closed at (?P<b>{_N})\.?$", re.I)),
    ("closure", "area", re.compile(rf"^(?P<a>{_N}) is closed in the area of (?P<b>{_N})\.?$")),
    ("closure", "segment", re.compile(rf"^(?P<a>{_N}) is closed between (?P<x>{_N}) and (?P<y>{_N})\.?$")),
    ("closure", "whole", re.compile(rf"^All of (?P<a>{_N}) (?:is |has been )?closed(?: (?:because of|due to) flooding)?\.?$")),
    ("closure", "road", re.compile(rf"^(?P<a>{_N}) (?:is |has been )?closed(?: (?:because of|due to) flooding)?\.?$")),
    ("open", "segment", re.compile(rf"^(?P<a>{_N}) is (?:now )?(?:clear|open) from (?P<x>{_N}) to (?P<y>{_N})\.?$")),
    ("open", "point", re.compile(rf"^(?P<a>{_N}) at (?P<b>{_N}) is (?:now )?(?:clear|open)\.?$")),
    ("open", "whole", re.compile(rf"^(?:All of )?(?P<a>{_N}) is (?:now )?(?:clear|open)(?:, but there (?:is|are) (?:some )?[a-z ]{{3,60}} on the shoulder)?\.?$")),
]
# A list under "The below roads/intersections are closed:" names each closure bare: "A and B", or a road alone.
LEAD = re.compile(r"\b(?:below|following) (?:roads?|intersections?)(?:\s*/\s*(?:roads?|intersections?))? (?:are|is) closed:", re.I)
# any "The below/following ...:" heading starts a new list section; only these two say what their items are
SECTION = re.compile(r"\b(?:below|following) [^:.]{3,80}:", re.I)
FLOODLEAD = re.compile(r"\b(?:below|following) (?:roads?|intersections?)(?:\s*/\s*(?:roads?|intersections?))? (?:are|is) "
                       r"experiencing (?:severe )?flooding:", re.I)
LISTITEM = re.compile(rf"^(?P<a>{_NL})(?: and (?P<b>{_NL}))?(?: \([A-Za-z ]{{3,40}}\))?$")
BOILER = re.compile(r"^(?:Road Closures:|Current Road Closures:|Please drive with extreme caution\.|Do not drive around barricades\.|"
                    r"Please drive with caution\.|Public Notices|Tagged as:.*|Back to top)$")
ROADISH = re.compile(r"\b(?:St|Street|Rd|Road|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Pkwy|Parkway|Hwy|Highway|Cswy|Causeway|"
                     r"Ln|Lane|U\.S\. ?\d+|US ?\d+|SR ?\d+|I-\d+)\b")


def norm(name: str) -> str:
    n = E.road_words(name)
    return ALIASES.get(n, n)


def core(name: str) -> str:
    """The road's name without its suffix word: 'babcock street' -> 'babcock'. Directional prefixes are kept."""
    words = norm(name).split()
    return " ".join(words[:-1] if len(words) > 1 and words[-1] in SUFFIX_ONLY else words)


def sentences(unit: str) -> list[str]:
    """Split on sentence ends, never after the abbreviations in road names (Dr., Jr., St., U.S.)."""
    return [s.strip() for s in re.split(r"(?<!\bDr\.)(?<!\bJr\.)(?<!\bSt\.)(?<!\bS\.)(?<=[.!?])\s+(?=[A-Z])", unit) if s.strip()]


def list_item(u: str, claim: str = "closure") -> dict | None:
    m = LISTITEM.match(u)
    if not m or E.SENSITIVE.search(u) or E.CRASH.search(u) or not (ROADISH.search(u) or len(u.split()) <= 2):
        return None
    if m["b"]:
        return {"claim": claim, "scope": "point", "road": core(m["a"]), "cross": [core(m["b"])], "dirs": []}
    return {"claim": claim, "scope": "road", "road": core(m["a"]), "cross": [], "dirs": []}


def classify(s: str) -> dict | None:
    if E.SENSITIVE.search(s) or E.CRASH.search(s):
        return None
    for claim, scope, rx in SENTENCES:
        m = rx.match(s)
        if m:
            d = m.groupdict()
            dirs = sorted({DIRS[w.lower()[:-5]] for w in re.findall(_D, d.get("dirs") or "", re.I)})
            cross = [core(d[k]) for k in ("b", "x", "y") if d.get(k)]
            return {"claim": claim, "scope": scope, "road": core(d["a"]), "cross": cross, "dirs": dirs}
    return None


LONGDIR = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB"}


def pin_shape(fields: dict) -> dict:
    """The place, scope and directions a pin states: its own dir field plus direction words in its wording."""
    where = re.sub(r"\[[^\]]*\]", " ", fields.get("where") or fields.get("road") or "")
    dirs = set(re.findall(r"\b(NB|SB|EB|WB)\b", where, re.I))
    dirs |= {LONGDIR[w.lower()] for w in re.findall(r"\b(?:north|south|east|west)bound\b", where, re.I)}
    dirs |= {x for x in re.findall(r"\b(NB|SB|EB|WB)\b", str(fields.get("dir") or ""), re.I)}
    dirs |= {LONGDIR[w.lower()] for w in re.findall(r"\b(?:north|south|east|west)bound\b", str(fields.get("dir") or ""), re.I)}
    dirs = sorted(d.upper() for d in dirs)
    every = bool(re.search(r"\ball directions\b", where, re.I))
    clean = re.sub(r"\([^)]*\)|\b(?:NB|SB|EB|WB)\b|\b(?:north|south|east|west)bound\b|\bat their intersection\b|"
                   r",?\s*all directions\b", " ", where, flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip()
    m = re.match(r"^All of (?P<a>.+)$", clean)
    if m:
        return {"scope": "whole", "road": core(m["a"]), "cross": [], "dirs": dirs, "every": every}
    m = re.match(r"^(?P<a>.+?) between (?P<x>.+?) and (?P<y>.+?)$", clean)
    if m:
        return {"scope": "segment", "road": core(m["a"]), "cross": [core(m["x"]), core(m["y"])], "dirs": dirs, "every": every}
    parts = [core(p) for p in re.split(r"\s+(?:at|and)\s+", clean) if p.strip()]
    if len(parts) == 2:
        return {"scope": "point", "road": parts[0], "cross": [parts[1]], "dirs": dirs, "every": every}
    return {"scope": "road", "road": core(fields.get("road") or clean), "cross": [], "dirs": dirs, "every": every}


def located(fields: dict) -> bool:
    """On the map: finite coordinates inside the county box (the page draws nothing, line included, without them)."""
    try:
        lat, lng = float(fields.get("lat")), float(fields.get("lng"))
    except (TypeError, ValueError):
        return False
    box = E.BREVARD_BOX
    return box[0] <= lat <= box[1] and box[2] <= lng <= box[3]


def page_closed(fields: dict) -> bool:
    return fields.get("status") == "closed" or bool(fields.get("fullClosure"))


def judge(c: dict, active: dict) -> tuple[str, list[str]]:
    on_road, same, wrong_dir, no_dir = [], [], [], []
    for rid, r in active.items():
        p = pin_shape(r["fields"])
        if c["road"] not in {p["road"], *p["cross"]}:
            continue
        on_road.append(rid)
        scope = "point" if c["scope"] == "area" else c["scope"]
        if scope == "road":  # a road named alone: only a pin that also names only that road says the same
            match = p["scope"] == "road" and p["road"] == c["road"]
        elif scope == "point":  # an intersection: the two names in either order
            match = p["scope"] == "point" and {p["road"], *p["cross"]} == {c["road"], *c["cross"]}
        else:  # whole road or stretch: the same main road, and the same two ends for a stretch
            match = p["scope"] == scope and p["road"] == c["road"] and sorted(p["cross"]) == sorted(c["cross"])
        if not match:
            continue
        # "A/B- eastbound" does not say which road the direction is on: a directional pin covers it only with exactly
        # those directions; a pin that states no direction is not assumed to cover it unless it says all directions.
        if c["dirs"] and not p["every"]:
            if not p["dirs"]:
                no_dir.append(rid)
                continue
            if set(c["dirs"]) != set(p["dirs"]):
                wrong_dir.append(rid)
                continue
        same.append(rid)
    if c["claim"] == "open":
        return ("reopen-review" if on_road else "no-active-pin"), on_road
    # a flooding claim is matched by a pin showing flooding or more; a closure claim only by a closed pin
    shows = (lambda f: page_closed(f) or E.rank(f) >= E.rank({"status": "flooded"})) if c["claim"] == "flooding" else page_closed
    closed = [r for r in same if shows(active[r]["fields"])]
    if closed:
        shown = [r for r in closed if located(active[r]["fields"])]
        if not shown:
            return "unlocated", closed
        return ("covered-road" if c["scope"] == "road" else "covered"), shown
    for verdict, rids in (("weaker", same), ("direction-unstated", no_dir), ("direction", wrong_dir), ("partial", on_road)):
        if rids:
            return verdict, rids
    return "missed", []


def fetch(url: str, host: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname != host or p.port or p.username:
        raise ValueError("unexpected source")
    req = urllib.request.Request(url, headers={"User-Agent": V.UA})
    with urllib.request.build_opener(V.SameHost()).open(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def read_post(page: str, now: datetime.datetime) -> dict:
    """The one dated post on the page: its date, heading time and sentences, all from that same post block."""
    parser = V.Posts()
    parser.feed(re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I))
    flat = lambda parts: re.sub(r"\s+", " ", " ".join(parts).replace("\xa0", " ")).strip()
    dated = [i for i, parts in enumerate(parser.posts) if len(list(V.PUBLISHED.finditer(flat(parts)))) == 1]
    if len(dated) != 1:
        return {"error": "layout-unsupported"}
    i = dated[0]
    m = V.PUBLISHED.search(flat(parser.posts[i]))
    date = datetime.datetime.strptime(" ".join(m.groups()), "%B %d %Y").strftime("%Y-%m-%d")
    text = flat(parser.posts[i])
    units = [flat(u) for j, u in parser.units if j == i and flat(u)]
    body = [u for u in units if not V.PUBLISHED.search(u) and not V.HEADING.fullmatch(u) and not BOILER.match(u)]
    # each list item belongs to the section heading above it: closed, severe flooding, or another (unread) kind
    heads = [(m.start(), "closure" if LEAD.fullmatch(m.group(0)) else "flooding" if FLOODLEAD.fullmatch(m.group(0)) else None)
             for m in SECTION.finditer(text)]
    listed, at = {}, 0
    for u in body:
        pos = text.find(u, at)
        at = pos + len(u) if pos >= 0 else at
        kind = [k for h, k in heads if h < pos]
        if kind and kind[-1]:
            listed[u] = kind[-1]
    time = V.stated_time(units, body[-1], date) if body else ""
    if (time or date) > (E.iso(now) if time else now.astimezone(E.ET).strftime("%Y-%m-%d")):
        return {"error": "future-time", "date": date}
    items = []  # (text, claim or None); a paragraph or list item is the unit whose whole meaning must be read
    for u in body:
        c = list_item(u, listed[u]) if u in listed else None
        if c:
            items.append((u, c))
            continue
        parts = [(x, classify(x)) for x in sentences(u) if not BOILER.match(x)]
        unread = [x for x, k in parts if not k]
        if not unread:
            items += parts
        elif any(k and k["claim"] == "open" for _, k in parts) and not any(k and k["claim"] == "closure" for _, k in parts):
            # an open statement with other sentences around it stays a review candidate, marked with its context
            items += [(x, dict(k, context="other sentences in the same paragraph")) for x, k in parts if k]
            items += [(x, None) for x in unread]
        else:
            items.append((u, None))  # a closure beside unread context ("Exercise scenario.") is not a closure claim
    return {"date": date, "time": time, "items": items}


def _day(stamp: str) -> str:
    return E.parse(stamp).astimezone(E.ET).strftime("%Y-%m-%d") if "T" in stamp else stamp


def after(a: str, b: str) -> bool:
    """Stamp a is provably later than b (a stated time, or an Eastern publish date). A date-only stamp on the same day
    as another stamp cannot be ordered against it."""
    da, db = _day(a), _day(b)
    if da != db:
        return da > db
    return "T" in a and "T" in b and a > b


def place_key(c: dict) -> str:
    return "|".join([c["scope"], c["road"], *sorted(c["cross"]), *c["dirs"]])


def run(now: datetime.datetime, prev: dict | None = None, get=fetch, events_path: str = E.EVENTS) -> dict:
    prev = prev or {}
    records = E.derive(E.load(events_path), now)["records"]
    active = {k: r for k, r in records.items() if r["status"] == "active"}
    # accepted official reopenings in our history, as whole-road open claims with their receipts
    official = []
    for rid, r in records.items():
        v = r.get("evidence") or {}
        k = classify(v.get("quote") or "") if r["status"] == "reopened" and v.get("kind") == "official" else None
        if k and k["claim"] == "open" and k["scope"] == "whole":
            official.append({"road": k["road"], "at": r["reopenedAt"], "rid": rid, "url": v.get("url"), "quote": v.get("quote")})
    out = {"checkedAt": E.iso(now), "fresh": True, "pilot": "City of Melbourne", "pending": PENDING,
           "sources": [], "unavailable": [], "posts": dict(prev.get("posts") or {})}
    for src in SOURCES:
        try:
            index = get(src["index"], src["host"])
        except Exception as exc:
            out["unavailable"].append({"url": src["index"], "state": "unreadable", "error": type(exc).__name__})
            continue
        links = []
        for href in re.findall(r'href="([^"]+)"', index):
            u = urllib.parse.urldefrag(urllib.parse.urljoin(src["index"] + "/", href))[0]
            if u.startswith(src["index"] + "/") and len(u) > len(src["index"]) + 1 and u not in links:
                links.append(u)
        if not links:
            out["unavailable"].append({"url": src["index"], "state": "layout-unsupported"})
            continue
        partial = True  # until the index reaches a post from before the episode, within the discovery budget
        rechecks = 0
        for n, u in enumerate(links[:DISCOVER]):
            known = out["posts"].get(u)
            if known and known.get("outside"):
                partial = False
                break  # the index lists newest first
            if known and n >= REFETCH and not known.get("error"):
                age = now - (E.parse(known.get("readAt")) or now)
                if age < datetime.timedelta(hours=RECHECK_HOURS) or rechecks >= RECHECK_PER_RUN:
                    continue
                rechecks += 1
            try:
                page = get(u, src["host"])
            except Exception as exc:  # the last good claims from this page stay, with the failed check recorded
                if known:
                    out["posts"][u] = dict(known, lastError={"at": E.iso(now), "state": "unreadable"})
                out["unavailable"].append({"url": u, "state": "unreadable", "error": type(exc).__name__})
                continue
            post = read_post(page, now)
            if post.get("error"):
                out["posts"][u] = (dict(known, lastError={"at": E.iso(now), "state": post["error"]})
                                   if known and known.get("claims") is not None else {"error": post["error"], "date": post.get("date")})
                out["unavailable"].append({"url": u, "state": post["error"]})
                continue
            if post["date"] < EPISODE_START:
                out["posts"][u] = {"date": post["date"], "outside": True}
                partial = False
                break
            claims, review = [], []
            for s, c in post["items"]:
                h = hashlib.sha256(s.encode()).hexdigest()
                if c:
                    claims.append(dict(c, sentence=s, sha256=h))
                else:
                    review.append(h)  # every unit is accounted for; unread ones are kept as a hash only
            out["posts"][u] = {"agency": src["agency"], "date": post["date"], "time": post["time"], "claims": claims,
                               "needsReview": review, "sha256": hashlib.sha256(page.encode()).hexdigest(),
                               "readAt": E.iso(now)}
            if not claims and not review:
                out["unavailable"].append({"url": u, "state": "no-supported-sentences"})
        found = {}
        for u, p in out["posts"].items():
            if p.get("agency") != src["agency"] or p.get("error") or p.get("outside"):
                continue
            stamp = p["time"] or p["date"]
            for c in p["claims"]:
                found.setdefault(place_key(c), []).append(dict(c, stamp=stamp, url=u))
        latest = {}
        for k, cs in found.items():
            # the newest claims are those no other claim is provably later than; differing ones stay a contradiction
            top = [c for c in cs if not any(after(o["stamp"], c["stamp"]) for o in cs)]
            top.sort(key=lambda c: ("T" in c["stamp"], c["stamp"]), reverse=True)
            latest[k] = dict(top[0])
            if len({c["claim"] for c in top}) > 1:
                latest[k].update(ambiguous=True, evidence=[{"source": c["url"], "sentence": c["sentence"],
                                                            "claim": c["claim"], "sourceTime": c["stamp"]} for c in top])
        stamps = [p["time"] or p["date"] for p in out["posts"].values()
                  if p.get("agency") == src["agency"] and not p.get("error") and not p.get("outside")]
        opens = [c for c in latest.values() if c["claim"] == "open" and c["scope"] == "whole" and not c.get("context")
                 and not c.get("ambiguous")]
        rows = []
        for k, c in sorted(latest.items()):
            resolved = None
            if c["claim"] in ("closure", "flooding") and not c.get("ambiguous"):
                # a later whole-road open statement for this road, from the city itself or accepted in our history
                later = [o for o in opens if o["road"] == c["road"] and after(o["stamp"], c["stamp"])]
                acc = [o for o in official if o["road"] == c["road"] and after(o["at"], c["stamp"])]
                if later:
                    resolved = {"verdict": "resolved-by-city", "by": {"source": later[0]["url"], "sentence": later[0]["sentence"],
                                                                      "sourceTime": later[0]["stamp"]}}
                elif acc:
                    resolved = {"verdict": "resolved-official", "by": {"rid": acc[0]["rid"], "source": acc[0]["url"],
                                                                       "quote": acc[0]["quote"], "reopenedAt": acc[0]["at"]}}
            if resolved:
                v, pins = resolved["verdict"], []
            else:
                v, pins = ("ambiguous", []) if c.get("ambiguous") else judge(c, active)
            rows.append({"place": k, "claim": c["claim"], "scope": c["scope"], "road": c["road"], "cross": c["cross"],
                         "dirs": c["dirs"], "sentence": c["sentence"], "sha256": c["sha256"], "source": c["url"],
                         "sourceTime": c["stamp"], "inNewest": not any(after(x, c["stamp"]) for x in stamps), "verdict": v, "pins": pins,
                         **({"resolvedBy": resolved["by"]} if resolved else {}),
                         **({"context": c["context"]} if c.get("context") else {}),
                         **({"evidence": c["evidence"]} if c.get("evidence") else {})})
        summary = {}
        for r in rows:
            summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
        summary["older-unresolved"] = sum(1 for r in rows if r["claim"] in ("closure", "flooding") and not r["inNewest"]
                                          and r["verdict"] not in ("covered", "covered-road", "resolved-by-city", "resolved-official"))
        summary["needs-review"] = sum(len(p.get("needsReview") or []) for p in out["posts"].values()
                                      if p.get("agency") == src["agency"])
        out["sources"].append({"agency": src["agency"], "index": src["index"], "partial": partial, "rows": rows,
                               "summary": summary})
    return out


def main() -> int:
    try:
        prev = json.load(open(OUT))
    except (OSError, ValueError):
        prev = {}
    try:
        rep = run(E.utcnow(), prev if isinstance(prev, dict) else {})
    except Exception as exc:  # keep the last good inventory, mark it stale, never stop the map
        rep = dict(prev if isinstance(prev, dict) else {}, fresh=False,
                   lastFailure={"at": E.iso(E.utcnow()), "error": type(exc).__name__})
        print("coverage failed:", type(exc).__name__, file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT + ".tmp", "w") as f:
            json.dump(rep, f, indent=1)
            f.write("\n")
        os.replace(OUT + ".tmp", OUT)
    except OSError as exc:
        print("coverage not written:", type(exc).__name__, file=sys.stderr)
    for s in rep.get("sources") or []:
        print("coverage:", s["agency"], s["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
