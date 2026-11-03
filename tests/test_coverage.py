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
        "Exercise scenario. All of Eber Road is closed.",
        "Florida Ave/Dairy Rd- eastbound remains closed with barricades blocking the roadway.",
        "Sarno Road is open, but there are some infrastructure issues on the shoulder.",
        "Malabar Rd/Minton Rd- remains closed with barricades blocking the roadway.",
        "Croton Rd/Post Rd- remains closed with barricades blocking the roadway.",
        "Please drive with extreme caution."]),
    IDX + "/R1020": post("10:20 a.m.", [
        "Babcock Street is clear from U.S. 1 to Palm Bay Road. However, the neighborhoods off of Babcock Street are "
        "still experiencing flooding issues.",
        "Eber Blvd/Babcock St- southbound and eastbound remains closed with barricades blocking the roadway."]),
    IDX + "/N2": post("", ["Lipscomb Street and University Boulevard", "Golfclub",
                           "Dr. Martin Luther King, Jr. Boulevard and NASA Boulevard", "Sarno Road",
                           "Wickham Road"], day="02",
                      lead="<strong>The below roads/intersections are closed:</strong>"),
    IDX + "/Old": post("8:00 a.m.", ["Old Rd/Gone St- remains closed."], day="01"),
}


def ev(rid, fields, src="manual"):
    return {"kind": "reported", "rid": rid, "src": src, "at": "2026-11-03T10:00:00Z", "stime": "2026-11-03T10:00:00Z",
            "actor": "t", "eid": rid, "fields": dict({"lat": 28.0, "lng": -80.6}, **fields)}


EVENTS = [ev("m:fl", {"road": "Babcock St", "where": "Babcock St at Florida Ave", "status": "closed", "dir": "WB"}),
          ev("m:sun", {"road": "Babcock St", "where": "Babcock St NB and Sun Lakes Rd EB at their intersection", "status": "closed"}),
          ev("m:eber", {"road": "Babcock St", "where": "Babcock St SB and Eber Blvd EB at their intersection", "status": "closed"}),
          ev("m:aur", {"road": "Aurora Rd", "where": "Aurora Rd at Wickham Rd", "dir": "WB", "status": "closed"}),
          ev("m:avb", {"road": "East Ave B", "where": "East Ave B between Babcock St and Brevard Ave", "status": "closed"}),
          ev("m:dfl", {"road": "Dairy Rd", "where": "Dairy Rd at Florida Ave", "status": "closed"}),
          ev("m:dairy", {"road": "Dairy Rd", "where": "Dairy Rd", "status": "closed"}),
          ev("m:mal", {"road": "Malabar Rd", "where": "Malabar Rd at Minton Rd", "status": "closed", "lat": None, "lng": None}),
          ev("m:wick", {"road": "Wickham Rd", "where": "Wickham Rd", "status": "closed", "lat": None, "lng": None}),
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
check("a directional closure with a pin whose own dir field gives the same direction is covered",
      row("point|florida|babcock|WB")["verdict"] == "covered" and row("point|florida|babcock|WB")["inNewest"])
check("a directional closure with a pin that states no direction is direction-unstated, not covered",
      row("point|florida|dairy|EB")["verdict"] == "direction-unstated")
check("reviewed alias: 'Sunlake Rd' is the pin's 'Sun Lakes Rd'", row("point|sun lakes|babcock")["pins"] == ["m:sun"])
check("Codex repro: EASTBOUND claim, pin 'Aurora Rd at Wickham Rd' with dir=WB is a direction mismatch",
      row("point|aurora|wickham|EB")["verdict"] == "direction")
check("long-form 'westbound' in a pin's wording is a direction, not part of the name",
      C.pin_shape({"where": "Aurora Rd westbound at Wickham Rd"})["dirs"] == ["WB"]
      and C.pin_shape({"where": "Aurora Rd westbound at Wickham Rd"})["road"] == "aurora")
check("Codex repro: Babcock between East Ave B and Brevard Ave is not covered by East Ave B between Babcock and Brevard",
      row("segment|babcock|brevard|east avenue b")["verdict"] == "partial")
check("Codex repro: 'All of Dairy Rd' is not covered by a pin that just names Dairy Rd", row("whole|dairy")["verdict"] == "partial")
check("'Dubber Road closed because of flooding' is read as the road (extent not stated); a flooded full-closure pin that "
      "also names only the road counts as closed", row("road|dubber")["verdict"] == "covered-road")
check("Codex repro: a closed pin with no map location is unlocated, not covered",
      row("point|malabar|minton")["verdict"] == "unlocated" and row("road|wickham")["verdict"] == "unlocated")
check("a genuinely new closure with no pin is missed", row("point|croton|post")["verdict"] == "missed")
check("Codex repro: a closure beside unread context in the same paragraph ('Exercise scenario.') is not a claim",
      not any(r["road"] == "eber" and r["claim"] == "closure" and r["scope"] == "whole" for r in src["rows"]))
check("a closure paragraph with an injury sentence is not a claim; the whole paragraph is a hash",
      not any(r["road"] == "hibiscus" for r in src["rows"]))
check("a 'clear from A to B' statement lists pins for review only, keeping its stretch",
      row("segment|eber|babcock|dairy")["verdict"] == "reopen-review" and row("segment|eber|babcock|dairy")["cross"] == ["babcock", "dairy"])
check("an open statement with other sentences around it stays a review candidate, marked with its context",
      row("segment|babcock|palm bay|u s 1").get("context") == "other sentences in the same paragraph")
check("an earlier same-day closure the newest update no longer lists stays, marked not in the newest update",
      row("point|eber|babcock|EB|SB")["verdict"] == "covered" and row("point|eber|babcock|EB|SB")["inNewest"] is False
      and src["summary"]["older-unresolved"] >= 1)
check("Codex repro: Sarno, listed closed Nov 2 and said open by the city Nov 3, is resolved-by-city, not missed",
      row("road|sarno")["verdict"] == "resolved-by-city" and "Sarno Road is open" in row("road|sarno")["resolvedBy"]["sentence"])
check("a list under 'The below roads/intersections are closed:' is read item by item (Dr. ... Jr. kept whole)",
      row("point|lipscomb|university")["verdict"] == "weaker" and row("road|golfclub")["verdict"] == "covered-road"
      and row("point|drive martin luther king jr|nasa")["verdict"] == "missed")
check("a negated closure ('no longer closed') is never a closure claim", not any("no longer" in r["sentence"] for r in src["rows"]))
blob = json.dumps(rep)
check("unvetted prose is never published: injury and exercise paragraphs are hashes only, and the sidebar is not the post",
      "injured" not in blob and "driver" not in blob and "Exercise" not in blob and "Sidebar" not in blob
      and src["summary"]["needs-review"] >= 3)
check("posts before the episode are not read, and the index walk stops there",
      "Old Rd" not in blob and IDX + "/Older" not in calls and src["partial"] is False)
check("fragment links (#main-content) are not posts", not any("#" in u for u in rep["posts"]))
check("it never writes the closure history", open(path).read() == before)
check("it reads only the official host", all(c.startswith("https://www.melbourneflorida.org/") for c in calls))
# Next day: the Nov 3 notices are still the latest word, so their closures are still accounted for
calls.clear()
NEXT = NOW + datetime.timedelta(days=1)
C.REFETCH = 1
rep2 = C.run(NEXT - datetime.timedelta(hours=23, minutes=30), rep, getter(PAGES), path)
check("older posts read recently are not fetched again; the newest is", IDX + "/N2" not in calls and IDX + "/R1045" in calls)
check("next day: yesterday's unresolved city closures are still in the inventory and still judged",
      any(r["place"] == "point|malabar|minton" for r in rep2["sources"][0]["rows"]))
calls.clear()
fixed = dict(PAGES, **{IDX + "/N2": post("", ["Golfclub"], day="02",
                                          lead="<strong>The below roads/intersections are closed:</strong>")})
rep2b = C.run(NEXT, rep2, getter(fixed), path)
check("Codex repro: an old URL the city corrects in place is re-read within a day and its removed claim goes",
      IDX + "/N2" in calls and not any(r["place"] == "point|lipscomb|university" for r in rep2b["sources"][0]["rows"]))
rep2c = C.run(NEXT + datetime.timedelta(hours=13), rep2b, getter({IDX: PAGES[IDX], IDX + "/R1045": PAGES[IDX + "/R1045"]}), path)
check("a re-check that fails keeps the page's last good claims and records the failure",
      rep2c["posts"][IDX + "/N2"].get("lastError", {}).get("state") == "unreadable"
      and any(r["place"] == "road|golfclub" for r in rep2c["sources"][0]["rows"]))
C.REFETCH = 3
# An accepted official reopening in our own history resolves an older city closure of that road
hp = os.path.join(tmp, "h.jsonl")
hist = [ev("m:sarno", {"road": "Sarno Rd", "where": "Sarno Rd at Eau Gallie Blvd", "status": "closed"}),
        {"kind": "cleared", "rid": "m:sarno", "src": "manual", "at": "2026-11-03T14:00:00Z", "actor": "t", "eid": "c1",
         "evidence": {"kind": "official", "url": "https://www.melbourneflorida.org/x", "quote": "Sarno Road is open.",
                      "publishedDate": "2026-11-03", "sourceTime": "2026-11-03T13:30:00Z", "reopenedAt": "2026-11-03T13:30:00Z"}}]
hist[0]["at"] = hist[0]["stime"] = "2026-11-02T10:00:00Z"
open(hp, "w").write("".join(json.dumps(e) + "\n" for e in hist))
only = {IDX: f'<a href="{IDX}/N2">c</a><a href="{IDX}/Old">d</a>', IDX + "/N2": post("", ["Sarno Road"], day="02",
        lead="<strong>The below roads/intersections are closed:</strong>"), IDX + "/Old": PAGES[IDX + "/Old"]}
r7 = C.run(NOW, None, getter(only), hp)["sources"][0]["rows"]
check("an older city closure of a road our history reopened from an official statement is resolved-official, with receipt",
      r7[0]["verdict"] == "resolved-official" and r7[0]["resolvedBy"]["quote"] == "Sarno Road is open.")
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

# Codex #237: chronology a date-only same-day claim cannot be ordered against; ambiguous opens resolve nothing; line-only
# records are not drawn by the page
DFL = "Dairy Rd/Florida Ave- remains closed with barricades blocking the roadway."
def timeline(pages):
    idx = "".join(f'<a href="{IDX}/{k}">{k}</a>' for k in pages) + f'<a href="{IDX}/Old">o</a>'
    got = C.run(NOW, None, getter(dict({IDX: idx, IDX + "/Old": PAGES[IDX + "/Old"]},
                                       **{IDX + "/" + k: v for k, v in pages.items()})), path)
    rows = got["sources"][0]["rows"]
    return lambda key: next((r for r in rows if r["place"] == key), {})
t = timeline({"T": post("8:30 a.m.", ["Dairy Road is open."]), "D": post("", [DFL, "All of Dairy Rd is closed."])})
check("Codex repro: a same-day date-only closure is not resolved by a timed 8:30 opening (it may be later)",
      t("point|dairy|florida")["verdict"] not in ("resolved-by-city", "resolved-official"))
check("Codex repro: a same-place date-only whole closure and a timed same-day opening stay a contradiction with both receipts",
      t("whole|dairy")["verdict"] == "ambiguous" and {e["claim"] for e in t("whole|dairy")["evidence"]} == {"open", "closure"})
t = timeline({"T": post("8:30 a.m.", ["Dairy Road is open."]), "D": post("", [DFL], day="02")})
check("a prior-day closure on Dairy and a current-day timed whole-Dairy opening may resolve",
      t("point|dairy|florida")["verdict"] == "resolved-by-city" and t("point|dairy|florida")["resolvedBy"]["sourceTime"] > "2026-11-03")
t = timeline({"A": post("8:30 a.m.", ["Dairy Road is open."]), "B": post("8:30 a.m.", ["All of Dairy Rd is closed."]),
              "D": post("", [DFL], day="02")})
check("Codex repro: an ambiguous whole-road opening resolves no other claim; the contradiction stays for review",
      t("whole|dairy")["verdict"] == "ambiguous" and t("point|dairy|florida")["verdict"] not in ("resolved-by-city", "resolved-official"))
check("Codex repro: a record with a line but no coordinates is not on the map (the page skips lat==null)",
      not C.located({"line": [[28.1, -80.6], [28.2, -80.6]]}) and not C.located({"lat": 40.0, "lng": -80.6, "line": [[28.1, -80.6]]})
      and C.located({"lat": 28.1, "lng": -80.6}))

# Codex #239: the real Nov 2 advisory layout - a closed list, then a NEW "severe flooding" heading and list; every one of
# the 22 entries keeps its own section (fixture mirrors the page's markup; no names of people)
CLOSED22 = ["Babcock Street and Avenue B", "Babcock Street and Florida Avenue", "Dairy Road and Snicole Road",
            "Dairy Road and Edgewood Drive", "Dubber Road", "Eau Gallie Boulevard and Sarno Road",
            "Riverside Drive (Southbound) is closed at Eau Gallie Boulevard", "Edgewood Road and Ruffner Road", "Golfclub",
            "Lake Washington Road and Stewart Road", "North Drive", "Snicole Road", "Stewart Road",
            "University Boulevard and Babcock Street"]
FLOOD22 = ["Eau Gallie Boulevard and Commodore Boulevard", "Dr. Martin Luther King, Jr. Boulevard and New Haven Avenue",
           "Dr. Martin Luther King, Jr. Boulevard and NASA Boulevard",
           "University Boulevard and Main Street (Briarwood Community)", "Dairy Road and Florida Avenue",
           "Lipscomb Street and University Boulevard", "Babcock Street and Southgate Boulevard", "US-192 and Country Club Road"]
ol = lambda xs: "<ol>" + "".join(f'<li><div role="presentation">{x}</div></li>' for x in xs) + "</ol>"
NOV2 = ('<html><body><article><p>Published on November 02, 2026</p>'
        '<div><span>Some roads are heavily flooded and completely impassable.</span></div>'
        '<div><span><strong>The below roads/intersections are closed:</strong></span></div>' + ol(CLOSED22) +
        '<div id="x_Signature"><div><strong>The below roads are experiencing severe flooding:</strong></div>' + ol(FLOOD22) +
        '<p>Please do not travel.</p></div></article></body></html>')
got = C.read_post(NOV2, NOW)
claims = [(u, c["claim"]) for u, c in got["items"] if c]
check("real Nov 2 layout: all 14 closed-list entries are closures and all 8 severe-flooding entries are flooding claims",
      [u for u, k in claims if k == "closure"] == CLOSED22 and [u for u, k in claims if k == "flooding"] == FLOOD22)
t = timeline({"N": NOV2})
check("a flooding claim is covered by a flooded pin (never called closed, never 'weaker' for lacking a closure)",
      t("point|lipscomb|university")["claim"] == "flooding" and t("point|lipscomb|university")["verdict"] == "covered")
other = C.read_post(NOV2.replace("experiencing severe flooding:", "under a detour:"), NOW)
check("an unrecognised list heading ends the closed list; its items are not read as closures",
      not any(c for u, c in other["items"] if u in FLOOD22))

print("failures:", fails)
sys.exit(1 if fails else 0)
