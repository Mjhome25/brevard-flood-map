#!/usr/bin/env python3
"""Official advisory coverage: every line of the newest city update is accounted for; read-only; failures visible."""
import datetime
import json
import os
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import coverage as C  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok  " if ok else "FAIL"), name)
    fails += not ok


tmp = tempfile.mkdtemp()
NOW = datetime.datetime(2026, 11, 3, 16, 0, tzinfo=datetime.timezone.utc)
IDX = C.SOURCES[0]["index"]


def post(time, lines, day="03"):
    lis = "".join(f"<li>{x}</li>" for x in lines)
    return (f"<html><body><article><p>Published on November {day}, 2026</p><p>Nov. {int(day)}, {time} traffic update:</p>"
            f"<ul>{lis}</ul></article></body></html>")


PAGES = {
    IDX: f'<a href="{IDX}/Road-Closures-1045">a</a><a href="{IDX}/Road-Closures-1020">b</a><a href="{IDX}/Old">c</a>',
    IDX + "/Road-Closures-1045": post("10:45 a.m.", [
        "Eber Road is clear from Babcock Street to Dairy Road.",
        "Florida Ave/Babcock St- westbound remains closed with barricades blocking the roadway.",
        "Sunlake Rd/Babcock St- remains closed with barricades blocking the roadway.",
        "Aurora Rd/Wickham Rd- eastbound remains closed with barricades blocking the roadway.",
        "Hibiscus Blvd/Dairy Rd- remains closed with barricades blocking the roadway.",
        "Please drive with extreme caution."]),
    IDX + "/Road-Closures-1020": post("10:20 a.m.", [
        "Babcock Street is clear from U.S. 1 to Palm Bay Road. However, the neighborhoods off of Babcock Street are "
        "still experiencing flooding issues.",
        "Babcock St/Sunlake Rd- northbound and eastbound remains closed with barricades blocking the roadway.",
        "Eber Blvd/Babcock St- southbound and eastbound remains closed with barricades blocking the roadway."]),
    IDX + "/Old": post("8:00 a.m.", ["Old Rd/Gone St- remains closed."], day="02"),
}


def ev(rid, fields, src="manual"):
    return {"kind": "reported", "rid": rid, "src": src, "at": "2026-11-03T10:00:00Z", "stime": "2026-11-03T10:00:00Z",
            "actor": "t", "eid": rid, "fields": dict({"lat": 28.0, "lng": -80.6}, **fields)}


EVENTS = [ev("m:fl", {"road": "Babcock St", "where": "Babcock St at Florida Ave", "status": "closed"}),
          ev("m:sun", {"road": "Babcock St", "where": "Babcock St NB and Sun Lakes Rd EB at their intersection", "status": "closed"}),
          ev("m:eber", {"road": "Babcock St", "where": "Babcock St SB and Eber Blvd EB at their intersection", "status": "closed"}),
          ev("m:aur", {"road": "Wickham Rd", "where": "Wickham Rd at Aurora Rd", "status": "flooded"}),
          ev("m:ebstack", {"road": "Eber Blvd", "where": "Eber Blvd at Stack Blvd", "status": "closed"})]
path = os.path.join(tmp, "e.jsonl")
open(path, "w").write("".join(json.dumps(e) + "\n" for e in EVENTS))
before = open(path).read()
calls = []


def get(url, host):
    calls.append(url)
    if url not in PAGES:
        raise OSError("not found")
    return PAGES[url]


rep = C.run(NOW, get, path)
src = rep["sources"][0]
by = {r["line"].split("-")[0].split(" is ")[0]: r for r in src["lines"]}
check("the newest same-day update is the 10:45 one", src["newest"]["url"].endswith("1045")
      and src["newest"]["time"] == "2026-11-03T15:45:00Z")
check("a closure line with a closed pin at that place is covered (Florida Ave/Babcock St)",
      by["Florida Ave/Babcock St"]["verdict"] == "covered" and by["Florida Ave/Babcock St"]["pins"] == ["m:fl"])
check("'Sunlake Rd' matches a pin written 'Sun Lakes Rd'", by["Sunlake Rd/Babcock St"]["pins"] == ["m:sun"])
check("a pin there that is not shown closed is weaker (a raise candidate)", by["Aurora Rd/Wickham Rd"]["verdict"] == "weaker")
check("a closure with no pin is missed (an add candidate)", by["Hibiscus Blvd/Dairy Rd"]["verdict"] == "missed")
check("a 'clear' line lists the active pins on that road as reopening candidates only",
      by["Eber Road"]["verdict"] == "reopen-candidates" and set(by["Eber Road"]["pins"]) == {"m:eber", "m:ebstack"})
check("a closure the newest update no longer lists is reported as dropped, by place not wording",
      [d["pins"] for d in src["dropped"]] == [["m:eber"]])
check("open statements from earlier same-day updates are kept", len(src["earlierOpen"]) == 1
      and "m:fl" in src["earlierOpen"][0]["pins"])
check("posts from another day are not current", all("Old Rd" not in r["line"] for r in src["lines"] + src["dropped"]))
check("the summary counts every accounted line", src["summary"] == {"covered": 2, "weaker": 1, "missed": 1,
                                                                     "reopen-candidates": 1, "dropped": 1})
check("it never writes the closure history", open(path).read() == before)
check("it reads only the official host", all(c.startswith("https://www.melbourneflorida.org/") for c in calls))
rep = C.run(NOW, lambda u, h: (_ for _ in ()).throw(OSError("down")), path)
check("an unreadable index is listed unavailable, never as zero closures",
      rep["unavailable"] and rep["unavailable"][0]["url"] == IDX and rep["sources"] == [])
try:
    C.fetch("https://evil.example/x", "www.melbourneflorida.org"); ok = False
except ValueError:
    ok = True
check("fetch refuses any other host", ok)
C.OUT = os.path.join(tmp, "audit", "coverage.json")
real = C.run
C.run = lambda *a, **k: 1 / 0
check("a crash never fails the job", C.main() == 0)
C.run = real

print("failures:", fails)
sys.exit(1 if fails else 0)
