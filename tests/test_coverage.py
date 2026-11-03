#!/usr/bin/env python3
"""Official advisory coverage: an episode inventory of every road claim, matched by place, scope and direction;
read-only; unvetted prose never published; unreadable or stale sources visible."""
import datetime
import json
import os
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import audit as A  # noqa: E402
import coverage as C  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok  " if ok else "FAIL"), name)
    fails += not ok


tmp = tempfile.mkdtemp()
NOW = datetime.datetime(2026, 11, 3, 16, 0, tzinfo=datetime.timezone.utc)
IDX = C.SOURCES[0]["index"]


def post(time, lines, day="03", lead=""):
    lis = "".join(f"<li>{x}</li>" for x in lines)
    head = f"<p>Nov. {int(day)}, {time} traffic update:</p>" if time else ""
    return (f"<html><body><nav><p>Sidebar Rd/Other St- remains closed.</p></nav><article><p>Published on November {day}, "
            f"2026</p>{head}<div>{lead}</div><ul>{lis}</ul></article></body></html>")


INJURY = "Hibiscus Blvd/Dairy Rd- remains closed. A driver was injured."
PAGES = {
    IDX: (f'<a href="{IDX}/#main-content">skip</a><a href="{IDX}/R1045">a</a><a href="{IDX}/R1020">b</a>'
          f'<a href="{IDX}/N2">c</a><a href="{IDX}/Old">d</a><a href="{IDX}/Older">e</a>'),
    IDX + "/R1045": post("10:45 a.m.", [
        "Eber Road is clear from Babcock Street to Dairy Road.",
        "Florida Ave/Babcock St- westbound remains closed with barricades blocking the roadway.",
        "Sunlake Rd/Babcock St- remains closed with barricades blocking the roadway.",
        "Aurora Rd/Wickham Rd- eastbound remains closed with barricades blocking the roadway.",
        INJURY,
        "Dubber Road closed because of flooding.",
        "Eber Road is no longer closed.",
        "All of Dairy Rd is closed.",
        "Babcock St- between East Ave B and Brevard Ave remains closed with barricades blocking the roadway.",
        "Please drive with extreme caution."]),
    IDX + "/R1020": post("10:20 a.m.", [
        "Babcock Street is clear from U.S. 1 to Palm Bay Road. However, the neighborhoods off of Babcock Street are "
        "still experiencing flooding issues.",
        "Eber Blvd/Babcock St- southbound and eastbound remains closed with barricades blocking the roadway."]),
    IDX + "/N2": post("", ["Lipscomb Street and University Boulevard", "Golfclub",
                           "Dr. Martin Luther King, Jr. Boulevard and NASA Boulevard"], day="02",
                      lead="<strong>The below roads/intersections are closed:</strong>"),
    IDX + "/Old": post("8:00 a.m.", ["Old Rd/Gone St- remains closed."], day="01"),
}


def ev(rid, fields, src="manual"):
    return {"kind": "reported", "rid": rid, "src": src, "at": "2026-11-03T10:00:00Z", "stime": "2026-11-03T10:00:00Z",
            "actor": "t", "eid": rid, "fields": dict({"lat": 28.0, "lng": -80.6}, **fields)}


EVENTS = [ev("m:fl", {"road": "Babcock St", "where": "Babcock St at Florida Ave", "status": "closed"}),
          ev("m:sun", {"road": "Babcock St", "where": "Babcock St NB and Sun Lakes Rd EB at their intersection", "status": "closed"}),
          ev("m:eber", {"road": "Babcock St", "where": "Babcock St SB and Eber Blvd EB at their intersection", "status": "closed"}),
          ev("m:aur", {"road": "Aurora Rd", "where": "Aurora Rd WB at Wickham Rd", "status": "closed"}),
          ev("m:avb", {"road": "Babcock St", "where": "Babcock St at East Ave B", "status": "closed"}),
          ev("m:dfl", {"road": "Dairy Rd", "where": "Dairy Rd at Florida Ave", "status": "closed"}),
          ev("m:dub", {"road": "Dubber Rd", "where": "Dubber Rd", "status": "flooded", "fullClosure": True}),
          ev("m:lip", {"road": "University Blvd", "where": "University Blvd at Lipscomb St", "status": "flooded"}),
          ev("m:golf", {"road": "Golfclub", "where": "Golfclub", "status": "closed"})]
path = os.path.join(tmp, "e.jsonl")
open(path, "w").write("".join(json.dumps(e) + "\n" for e in EVENTS))
before = open(path).read()
calls = []


def getter(pages):
    def get(url, host):
        calls.append(url)
        if url not in pages:
            raise OSError("not found")
        return pages[url]
    return get


rep = C.run(NOW, None, getter(PAGES), path)
src = rep["sources"][0]
row = lambda key: next((r for r in src["rows"] if r["place"] == key), {})
check("the newest update's directional closure with the same-direction pin is covered",
      row("point|florida|babcock|WB")["verdict"] == "covered" and row("point|florida|babcock|WB")["inNewest"])
check("reviewed alias: 'Sunlake Rd' is the pin's 'Sun Lakes Rd'", row("point|sun lakes road|babcock")["pins"] == ["m:sun"]
      if "sun lakes road" in C.core("Sunlake Rd") else row("point|sun lakes|babcock")["pins"] == ["m:sun"])
check("Codex repro: an EASTBOUND claim with only a WESTBOUND pin is a direction mismatch, not covered",
      row("point|aurora|wickham|EB")["verdict"] == "direction")
check("Codex repro: a stretch between Ave B and Brevard Ave with only an Ave B pin is partial, not covered",
      row("segment|babcock|brevard|east avenue b")["verdict"] == "partial")
check("Codex repro: 'All of Dairy Rd' with only a Dairy/Florida pin is partial, not covered", row("whole|dairy")["verdict"] == "partial")
check("'Dubber Road closed because of flooding' is read, and a flooded full-closure pin counts as closed on the page",
      row("whole|dubber")["verdict"] == "covered")
check("a closure with no pin is missed", row("point|hibiscus|dairy")["verdict"] == "missed")
check("a 'clear from A to B' statement lists pins for review only, keeping its stretch",
      row("segment|eber|babcock|dairy")["verdict"] == "reopen-review" and row("segment|eber|babcock|dairy")["cross"] == ["babcock", "dairy"])
check("an earlier same-day closure the newest update no longer lists stays, marked not in the newest update",
      row("point|eber|babcock|EB|SB")["verdict"] == "covered" and row("point|eber|babcock|EB|SB")["inNewest"] is False)
check("a list under 'The below roads/intersections are closed:' is read item by item (Dr. ... Jr. kept whole)",
      row("point|lipscomb|university")["verdict"] == "weaker" and row("road|golfclub")["verdict"] == "covered-road"
      and row("point|drive martin luther king jr|nasa")["verdict"] == "missed")
check("a negated closure ('no longer closed') is never a closure claim", not any("no longer" in r["sentence"] for r in src["rows"]))
blob = json.dumps(rep)
check("unvetted prose is never published: the injury sentence is a hash only, and the sidebar is not the post",
      "injured" not in blob and "driver" not in blob and "Sidebar" not in blob and src["summary"]["needs-review"] >= 3)
check("posts before the episode are not read, and the index walk stops there",
      "Old Rd" not in blob and IDX + "/Older" not in calls and src["partial"] is False)
check("fragment links (#main-content) are not posts", not any("#" in u for u in rep["posts"]))
check("it never writes the closure history", open(path).read() == before)
check("it reads only the official host", all(c.startswith("https://www.melbourneflorida.org/") for c in calls))
# Next day: the Nov 3 notices are still the latest word, so their closures are still accounted for
calls.clear()
NEXT = NOW + datetime.timedelta(days=1)
C.REFETCH = 1
rep2 = C.run(NEXT, rep, getter(PAGES), path)
C.REFETCH = 3
check("next day: yesterday's unresolved city closures are still in the inventory and still judged",
      any(r["place"] == "point|hibiscus|dairy" and r["verdict"] == "missed" for r in rep2["sources"][0]["rows"]))
check("next day: older posts already read are not fetched again; the newest few are", IDX + "/N2" not in calls
      and IDX + "/R1045" in calls)
# Failures are visible, never zero
rep3 = C.run(NOW, None, getter({IDX: "<html><body>Service unavailable</body></html>"}), path)
check("a 200 index with no post links is layout-unsupported, not zero closures",
      rep3["unavailable"] == [{"url": IDX, "state": "layout-unsupported"}] and rep3["sources"] == [])
rep4 = C.run(NOW, None, getter({}), path)
check("an unreadable index is unavailable", rep4["unavailable"][0]["state"] == "unreadable")
fut = dict(PAGES, **{IDX + "/R1045": post("11:45 a.m.", ["Florida Ave/Babcock St- remains closed."])})
rep5 = C.run(NOW, None, getter(fut), path)
check("a post whose update time is in the future is refused and listed",
      {"url": IDX + "/R1045", "state": "future-time"} in rep5["unavailable"])
same = dict(PAGES, **{IDX + "/R1020": post("10:45 a.m.", ["Eber Road is clear from Babcock Street to Dairy Road.",
                                                          "Eber Road/Babcock Street- remains closed."])})
same[IDX + "/R1045"] = post("10:45 a.m.", ["Eber Road is clear from Babcock Street to Dairy Road."])
rep6 = C.run(NOW, None, getter(same), path)
check("two posts with the same time are combined; each claim keeps its own place", any(
    r["place"] == "segment|eber|babcock|dairy" for r in rep6["sources"][0]["rows"]))
try:
    C.fetch("https://evil.example/x", "www.melbourneflorida.org"); ok = False
except ValueError:
    ok = True
check("fetch refuses any other host", ok)
# main keeps the last good inventory on a crash, marks it failed, and the ledger says so
C.OUT = os.path.join(tmp, "audit", "coverage.json")
os.makedirs(os.path.dirname(C.OUT), exist_ok=True)
json.dump(rep, open(C.OUT, "w"))
real = C.run
C.run = lambda *a, **k: 1 / 0
check("a crash never fails the job", C.main() == 0)
C.run = real
saved = json.load(open(C.OUT))
check("a crash keeps the last good claims and records the failure", saved["sources"] == json.loads(json.dumps(rep["sources"]))
      and saved["fresh"] is False and saved["lastFailure"]["error"] == "ZeroDivisionError")
check("the ledger line says the last check failed", "LAST CHECK FAILED" in A.coverage_line(C.OUT, NOW))
json.dump(rep, open(C.OUT, "w"))
check("the ledger line says when counts are stale", "STALE" in A.coverage_line(C.OUT, NOW + datetime.timedelta(hours=2))
      and "STALE" not in A.coverage_line(C.OUT, NOW))
check("the ledger line names the pilot and the pending sources", "Melbourne pilot" in A.coverage_line(C.OUT, NOW)
      and "WESH" in A.coverage_line(C.OUT, NOW))
check("a missing coverage file is unavailable in the ledger", A.coverage_line(os.path.join(tmp, "none.json"), NOW)
      == "Coverage: unavailable.")

print("failures:", fails)
sys.exit(1 if fails else 0)
