#!/usr/bin/env python3
"""reconcile.plan(): what a run adds to the closure history. A record is marked missing only when its source was read
successfully; a failed or suspiciously empty source changes nothing. Also: stable FHP ids and Eastern-time parsing.
  python3 -I tests/test_reconcile.py <repo>
"""
import ast
import json
import datetime
import os
import subprocess
import sys
import tempfile

repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, repo)
import events as E  # noqa: E402
import reconcile as R  # noqa: E402

fails = 0
T0 = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)
tmp = tempfile.mkdtemp(prefix="reconcile-test-")


def check(label, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + label)
    fails += not ok


def item(i, layer="Closures", **kw):
    it = {"id": f"fl511-{i}", "layer": layer, "status": "closed", "fullClosure": True, "road": "SR-507",
          "where": f"SR-507 NB at Street {i}", "reported": "2026-11-02T11:00:00Z", "updated": "2026-11-02T11:30:00Z"}
    it.update(kw)
    return it


def run(path, obs, hours):
    now = T0 + datetime.timedelta(hours=hours)
    state = E.derive(E.load(path), now)
    new, notes = R.plan(obs, state, E.iso(now))
    return E.append(new, path, now=now), notes, E.derive(E.load(path), now)["records"]


p = os.path.join(tmp, "a.jsonl")
obs = {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3), item(4)]}
w, _, recs = run(p, obs, 0)
check("first run reports four records", sum(e["kind"] == "reported" for e in w) == 4 and len(recs) == 4)
w, _, _ = run(p, obs, 1)
check("an identical second run adds nothing", w == [])

w, _, recs = run(p, {"sources": {"fl511": "down"}, "items": []}, 2)
check("feed down: nothing marked missing, all active", not any(e["kind"] == "missing" for e in w)
      and all(r["status"] == "active" and not r["missingSince"] for r in recs.values()))
w, notes, recs = run(p, {"sources": {"fl511": "empty"}, "items": []}, 3)
check("feed empty while we hold 4 of its records: treated as a fault, nothing missing, a note shown",
      not any(e["kind"] == "missing" for e in w) and notes and all(not r["missingSince"] for r in recs.values()))
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2)]}, 4)
check("feed up without two records: those two marked missing, still active",
      sum(e["kind"] == "missing" for e in w) == 2
      and all(r["status"] == "active" for r in recs.values()) and recs["brevard:fl511:3"]["missingSince"])
w, _, _ = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2)]}, 5)
check("still missing next run: no repeated missing events", not any(e["kind"] == "missing" for e in w))
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3)]}, 6)
check("a record listed again clears missingSince", recs["brevard:fl511:3"]["missingSince"] is None)
w, _, recs = run(p, {"sources": {"fl511": "up"}, "items": [item(1), item(2), item(3, status="caution", fullClosure=False)]}, 7)
check("a downgraded listing keeps the record closed and flags it",
      recs["brevard:fl511:3"]["fields"]["status"] == "closed" and recs["brevard:fl511:3"]["recheck"])

p2 = os.path.join(tmp, "b.jsonl")
run(p2, {"sources": {"fl511": "up"}, "items": [item(1)]}, 0)
w, _, recs = run(p2, {"sources": {"fl511": "empty"}, "items": []}, 1)
check("a valid empty feed with fewer than 3 open records marks them missing (still active)",
      any(e["kind"] == "missing" for e in w) and recs["brevard:fl511:1"]["status"] == "active")

d = R.display(E.derive(E.load(p)))[0]
check("display keeps every active record in the page schema", len(d) == 4 and all("where" in it and it["id"] for it in d))

# FHP roadblock wording (the old parser read 'ROADBLOCK' inside 'NO ROADBLOCK' as a full closure)
page_closed = lambda st, full: bool(full or st == "closed")  # template.html isClosed
tpl = open(os.path.join(repo, "template.html")).read()
check("the page's closed test is still fullClosure OR status closed",
      "const isClosed=it=>!!(it.fullClosure||it.status==='closed');" in tpl)
# (type, remark) -> (status, fullClosure, page shows closed); the updater uses fhp_classify for BOTH fields
cases = [("MVC", "No Roadblock", "caution", False, False), ("MVC", "No Road Block", "caution", False, False),
         ("MVC W/O ROADBLOCK", "", "caution", False, False), ("MVC WITHOUT ROADBLOCK", "", "caution", False, False),
         ("MVC", "Partial Roadblock - Use Caution", "caution", False, False),
         ("MVC W/Roadblock", "Partial Roadblock - Use Caution", "caution", False, False),
         ("MVC W/O Roadblock", "No Roadblock", "caution", False, False),
         ("MVC W/ROADBLOCK", "", "closed", False, True), ("MVC", "Roadblock", "closed", False, True),
         ("MVC", "Road Blocked", "closed", True, True), ("MVC", "All Lanes Blocked", "closed", True, True),
         ("ROAD CLOSED", "", "closed", True, True), ("MVC", "Roadway Open", "caution", False, False),
         ("MVC W/Roadblock", "Roadway Open", "caution", False, False), ("MVC", "Right lane blocked", "caution", False, False),
         ("MVC", "Water over roadway", "flooded", False, False),
         ("MVC", "Right lane closed", "caution", False, False), ("MVC", "Left two lanes closed", "caution", False, False),
         ("MVC", "Road not closed", "caution", False, False), ("MVC", "Not all lanes closed", "caution", False, False),
         ("MVC", "Right shoulder blocked", "caution", False, False), ("MVC", "Ramp closed", "caution", False, False),
         ("MVC W/Roadblock", "Right lane blocked", "caution", False, False),
         ("MVC", "All lanes closed NB, SB open", "caution", False, False),
         ("MVC", "Roadway closed", "closed", True, True), ("MVC", "All lanes are blocked", "closed", True, True),
         ("Road Closed Due To Water", "Southbound Lanes Under Water Use Caution", "flooded", True, True),
         ("Road Closed Due To Water", "", "flooded", True, True), ("Roadway Debris/Object", "Debris In The Roadway Use Caution",
                                                                 "caution", False, False)]
bad = [c for c in cases if (*R.fhp_classify(c[0], c[1]), page_closed(*R.fhp_classify(c[0], c[1]))) != c[2:]]
check(f"FHP rows: negated/partial roadblock is never closed on the page; full closure needs explicit wording ({bad})",
      not bad)
src = open(os.path.join(repo, "update_flood_data.py")).read()
check("the updater takes both FHP status and fullClosure from fhp_classify",
      "status, full = fhp_classify(typ, remark)" in src and '"status": status, "fullClosure": full' in src)
fix = R.fhp_normalize({"layer": "FHP", "title": "Mvc W/Roadblock at FAY BLVD (Partial Roadblock - Use Caution)",
                       "lanes": "Partial Roadblock - Use Caution", "status": "closed", "fullClosure": True})
check("migration normalizes an old misreading in both fields with an audit note",
      fix and fix["now"] == {"status": "caution", "fullClosure": False}
      and fix["was"] == {"status": "closed", "fullClosure": True} and "parser correction" in fix["reason"])
check("a correct FHP row is left alone", R.fhp_normalize({"layer": "FHP", "title": "Mvc at X", "lanes": "Road Blocked",
                                                          "status": "closed", "fullClosure": True}) is None)
p3 = os.path.join(tmp, "c.jsonl")
fhp = lambda **kw: dict({"id": "fhp-1", "layer": "FHP", "status": "caution", "road": "SR-528", "spot": "at MM52",
                         "reported": "2026-11-02T11:00:00Z", "updated": "2026-11-02T11:00:00Z", "title": "Mvc at SR-528"}, **kw)
run(p3, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=True, lanes="Roadblock")]}, 0)
w, _, recs = run(p3, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=False, lanes="No Roadblock",
                                                              updated="2026-11-02T12:00:00Z")]}, 1)
r3 = next(iter(recs.values()))
check("a genuine earlier roadblock followed by 'No Roadblock' stays a full closure, flagged",
      r3["fields"]["fullClosure"] and r3["recheck"])
p4 = os.path.join(tmp, "d.jsonl")
run(p4, {"sources": {"fhp": "up"}, "items": [fhp(fullClosure=False, lanes="Roadway Open")]}, 0)
it4 = R.display(E.derive(E.load(p4)))[0][0]
check("a 'Roadway Open' remark on an active record reads as an unverified dated note",
      it4["lanes"].startswith('Source said "Roadway Open"') and "not a verified reopening" in it4["lanes"])

# FHP CAD table: columns bound by header text; an incident with an Arrived time is still active
H = "".join(f"<th>{c}&nbsp;</th>" for c in R.FHP_COLUMNS)
row = lambda *c: '<tr class="dxgvDataRow">' + "".join(f"<td>{x}</td>" for x in c) + "</tr>"
page = (f"<table><tr>{H}</tr><tr><td>layout</td></tr>" + row("MVC", "11/03/2026 06:00:00", "11/03/2026 06:02:00", "11/03/2026 06:10:00",
                                     "BREVARD", "SR-520 x[SAMPLE RD] [COCOA]", "Right lane blocked", "28.3", "-80.7")
        + row("MVC", "11/03/2026 06:05:00", "", "", "ORANGE", "SR-50 x[X] [ORLANDO]", "", "28.5", "-81.3") + "</table>")
rows = R.fhp_rows(page)
check("an FHP incident with an Arrived time is kept (Arrived is not Cleared); other counties dropped",
      len(rows) == 1 and rows[0]["Arrived"] == "11/03/2026 06:10:00" and rows[0]["Remarks"] == "Right lane blocked")
swapped = page.replace("<th>Dispatched&nbsp;</th><th>Arrived&nbsp;</th>", "<th>Arrived&nbsp;</th><th>Dispatched&nbsp;</th>")
check("columns follow the header, not position", R.fhp_rows(swapped)[0]["Arrived"] == "11/03/2026 06:02:00")
try:
    R.fhp_rows(page.replace("Arrived", "Cleared")); ok = False
except ValueError:
    ok = True
check("a changed header set is a source failure, not shifted columns", ok)
upd = open(os.path.join(repo, "update_flood_data.py")).read()
check("the updater no longer drops incidents by a third time column", "t_clr" not in upd and "fhp_table(s)" in upd)
for broken, why in ((page.replace('<td>-80.7</td>', '', 1), "a Brevard row missing its last cell"),
                    (page.replace("SR-520 x[SAMPLE RD] [COCOA]", "", 1), "a row with no location"),
                    (page.replace("11/03/2026 06:00:00", "not-a-time", 1), "a row with an unreadable Received time"),
                    (page.replace("11/03/2026 06:00:00", "13/45/2026 06:00:00", 1), "a row with an impossible date")):
    try:
        R.fhp_table(broken); ok = False
    except ValueError:
        ok = True
    check(f"a broken incident row makes the whole FHP snapshot a source failure: {why}", ok)
only_orange = page.replace("BREVARD", "ORANGE")
check("a complete table with no Brevard rows is a successful read with zero Brevard incidents",
      len(R.fhp_table(only_orange)) == 2 and R.fhp_rows(only_orange) == [])
check("the updater calls FHP up from a complete table even with no Brevard rows",
      'sources["fhp"] = "up" if table else "empty"' in upd)

# Government verification (verify.issue): the page sentence must be about THIS road and impact; it only raises
import verify as V  # noqa: E402
NOWV = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=datetime.timezone.utc)
MEL = "https://www.melbourneflorida.org/Government/City-News/Traffic-Advisories/Road-Closures"
PAGE = ("<html><body><h1>Road Closures</h1><article><p>Published on November 02, 2026</p><p>All of Dairy Road is closed.</p>"
        "<p>Babcock Street at University Boulevard is closed.</p><p>Flooding on Minton Road at Norfolk Parkway.</p>"
        "<p>City Hall is open Monday through Friday.</p><p>Hollywood Boulevard is not closed.</p><p>Sarno Road will be "
        "closed.</p><p>A driver was injured on Eber Road at Dairy Road and Eber Road is closed.</p></article></body></html>")
pv = os.path.join(tmp, "v.jsonl")
man = lambda rid, **kw: dict({"id": "man-" + rid, "layer": "manual", "status": "flooded", "reported": "2026-11-02T12:00:00Z",
                              "updated": "2026-11-02T12:00:00Z", "source": "City of Melbourne (via Space Coast Daily)",
                              "sourceUrl": "https://www.melbourneflorida.org/"}, **kw)
run(pv, {"sources": {"manual": "up"}, "items": [
    man("dairy", road="Dairy Rd", spot="at Florida Ave", where="Dairy Rd at Florida Ave"),
    man("babcock", road="Babcock St", spot="at University Blvd", status="closed", fullClosure=True),
    man("babsouth", road="Babcock St", where="Babcock St at Southgate Blvd"),
    man("nocross", road="Babcock St", where="Babcock St"),
    man("minton", road="Minton Rd", spot="at Norfolk Pkwy"),
    man("eber", road="Eber Rd", spot="at Dairy Rd"),
    man("holly", road="Hollywood Blvd", spot="at Palm Bay Rd")]}, 0)
recv = lambda r: E.derive(E.load(pv), NOWV)["records"]["brevard:manual:" + r]
def refused(rid, quote, raise_to=None, page=PAGE, url=MEL, final=None):
    try:
        V.issue("brevard:manual:" + rid, url, quote, raise_to, page, final or url, NOWV, pv)
    except E.Refused as exc:
        return str(exc)
    return ""
dairy0 = [i for i in R.display(E.derive(E.load(pv), NOWV))[0] if i["id"] == "brevard:manual:dairy"][0]
check("a report whose link is a government homepage is still not government verified", dairy0["verified"] is None)
cases = [("minton", "City Hall is open Monday through Friday.", None, "an unrelated sentence"),
         ("dairy", "Eber Road is closed.", "closed", "a plain closure of a different road"),
         ("babsouth", "Babcock Street at University Boulevard is closed.", "closed", "same road, different cross street"),
         ("nocross", "Babcock Street at University Boulevard is closed.", "closed", "a report with no cross street"),
         ("holly", "Hollywood Boulevard is not closed.", None, "a negated sentence"),
         ("dairy", "Sarno Road will be closed.", None, "another road, future"),
         ("eber", "A driver was injured on Eber Road at Dairy Road and Eber Road is closed.", "closed", "personal detail"),
         ("dairy", "All of Dairy Road is closed.", None, "a closure sentence on a flood report without --closed"),
         ("dairy", "All of Dairy Road is closed. Not on page", None, "a sentence not on the page")]
for rid, q, rt, why in cases:
    check(f"verification refused: {why}", bool(refused(rid, q, rt)) and recv(rid).get("verified") is None)
check("verification refused: a plain-http URL", bool(refused("babcock", "Babcock Street at University Boulevard is closed.",
                                                     url=MEL.replace("https", "http"))))
check("verification refused: a redirect to another host", bool(refused(
    "babcock", "Babcock Street at University Boulevard is closed.", final="https://untrusted.example/x")))
check("verification refused: a page with no Published date", bool(refused(
    "babcock", "Babcock Street at University Boulevard is closed.", page=PAGE.replace("Published on", "Posted"))))
check("verification refused: a page dated in the future", bool(refused(
    "babcock", "Babcock Street at University Boulevard is closed.", page=PAGE.replace("2026", "2099"))))
check("a matching closure sentence verifies a closure report, dated by the page",
      not refused("babcock", "Babcock Street at University Boulevard is closed.")
      and recv("babcock")["verified"]["publishedDate"] == "2026-11-02" and recv("babcock")["status"] == "active")
check("a flooding sentence verifies a flood report without changing it",
      not refused("minton", "Flooding on Minton Road at Norfolk Parkway.")
      and recv("minton")["fields"]["status"] == "flooded" and recv("minton")["verified"])
check("a whole-road closure sentence raises a flood report to a full closure, cited (Mike, Nov 3)",
      not refused("dairy", "All of Dairy Road is closed.", "full")
      and recv("dairy")["fields"]["status"] == "closed" and recv("dairy")["fields"]["fullClosure"]
      and recv("dairy")["raisedBy"]["host"] == "www.melbourneflorida.org" and recv("dairy")["verified"])
run(pv, {"sources": {"manual": "up"}, "items": [man("dairy", road="Dairy Rd", spot="at Florida Ave",
                                                    where="Dairy Rd at Florida Ave")]}, 1)
check("the hand-entered report repeating 'flooding' after a cited raise is not flagged as reduced impact",
      recv("dairy")["fields"]["status"] == "closed" and not recv("dairy")["recheck"])
shown = [i for i in R.display(E.derive(E.load(pv), NOWV))[0] if i["id"] == "brevard:manual:dairy"][0]
check("the page shows the raised closure with its citation and page-dated badge",
      shown["raisedBy"]["agency"] == "City of Melbourne" and shown["verified"]["kind"] == "page")
check("check_events accepts the verified history", subprocess.run(
    [sys.executable, "-I", os.path.join(repo, "check_events.py"), "--before", "", "--bootstrap", "--events", pv],
    capture_output=True).returncode == 0)
good = [e for e in E.load(pv) if e["kind"] == "verified"][-1]
for bad, why in ((dict(good["evidence"], quote="Any unrelated statement about a driver"), "a personal quote"),
                 (dict(good["evidence"], publishedDate="not-a-date"), "a malformed date"),
                 (dict(good["evidence"], agency="Anything"), "an agency that does not match the host"),
                 ({k: v for k, v in good["evidence"].items() if k != "pageSha256"}, "a missing page hash")):
    try:
        E.append([{"rid": "brevard:manual:holly", "src": "manual", "kind": "verified", "actor": "x", "evidence": bad}], pv)
        ok = False
    except E.Refused:
        ok = True
    check(f"a raw verified event is refused for {why}", ok)
up = {"rid": "brevard:manual:dairy", "src": "manual", "kind": "relisted", "actor": "x", "stime": "2026-11-02T00:00:00Z",
      "fields": dict(recv("dairy")["fields"], status="caution", fullClosure=False), "raisedBy": good["evidence"]}
E.append([up], pv, now=NOWV)
run(pv, {"sources": {"manual": "up"}, "items": [man("dairy", road="Dairy Rd", spot="at Florida Ave", status="caution",
                                                    where="Dairy Rd at Florida Ave", updated="2026-11-03T09:00:00Z")]}, 2)
check("a NEW weaker report after a cited raise is flagged, closure kept",
      recv("dairy")["fields"]["status"] == "closed" and "source shows reduced impact" in recv("dairy")["recheck"])
check("a cited event cannot lower a closure (flagged instead)",
      recv("dairy")["fields"]["status"] == "closed" and recv("dairy")["recheck"])
# Codex #66: the subject is the road, the impact must match, only road wording is stored, the post is this episode
pq = os.path.join(tmp, "q.jsonl")
run(pq, {"sources": {"manual": "up"}, "items": [
    man("dairy", road="Dairy Rd", spot="at Florida Ave", where="Dairy Rd at Florida Ave"),
    man("dfull", road="Dairy Rd", spot="at Babcock St", status="closed", fullClosure=True)]}, 0)
recq = lambda r: E.derive(E.load(pq), NOWV)["records"]["brevard:manual:" + r]
def refusedq(rid, quote, raise_to=None, page=PAGE):
    before = open(pq).read()
    try:
        V.issue("brevard:manual:" + rid, MEL, quote, raise_to, page, MEL, NOWV, pq)
    except E.Refused as exc:
        return str(exc) if open(pq).read() == before else ""  # a refusal leaves the history untouched
    return ""
P2 = PAGE.replace("<p>All of Dairy Road is closed.</p>", "<p>All of Babcock Street is closed near Dairy Road.</p><p>All of "
                  "Dairy Road is flooded.</p><p>All of Dairy Road is closed, John Smith confirms.</p>")
check("refused: 'all of' belongs to another road that only mentions this one",
      refusedq("dairy", "All of Babcock Street is closed near Dairy Road.", "full", P2) and recq("dairy")["verified"] is None
      and recq("dairy")["fields"]["status"] == "flooded")
check("refused: a flooding sentence cannot verify a full closure",
      refusedq("dfull", "All of Dairy Road is flooded.", None, P2) and recq("dfull")["verified"] is None)
check("refused: a sentence carrying a person's name is never stored",
      refusedq("dairy", "All of Dairy Road is closed, John Smith confirms.", "full", P2)
      and "John" not in open(pq).read())
OLD = PAGE.replace("Published on November 02, 2026", "Published on January 1, 2020")
check("refused: a 2020 post is another event, not this November report",
      refusedq("dairy", "All of Dairy Road is closed.", "full", OLD) and recq("dairy")["verified"] is None)
SIDE = ("<html><body><article><p>Published on November 3, 2026</p><p>New unrelated city update.</p></article>"
        "<aside><h2>Historical notices</h2><p>All of Dairy Road is closed.</p></aside></body></html>")
check("refused: a sidebar notice does not borrow a separate article's date",
      refusedq("dairy", "All of Dairy Road is closed.", "full", SIDE))
SIB = ("<html><body><article><p>Published on November 3, 2026</p><p>Pool hours.</p></article>"
       "<article><p>All of Dairy Road is closed.</p></article></body></html>")
check("refused: an undated article does not borrow its sibling's date", refusedq("dairy", "All of Dairy Road is closed.", "full", SIB))
BEFORE = "<html><body><article><p>All of Dairy Road is closed.</p><p>Published on November 2, 2026</p></article></body></html>"
check("refused: a statement before its post's date line", refusedq("dairy", "All of Dairy Road is closed.", "full", BEFORE))
check("refused: a page with no article structure",
      refusedq("dairy", "All of Dairy Road is closed.", "full", PAGE.replace("<article>", "<div>").replace("</article>", "</div>")))
run(pq, {"sources": {"manual": "up"}, "items": [
    man("dairy", road="Dairy Rd", spot="at Florida Ave", where="Dairy Rd at Florida Ave"),
    man("dfull", road="Dairy Rd", spot="at Babcock St", status="closed", fullClosure=True),
    man("fwet", road="Dairy Rd", spot="at Babcock St", status="flooded", fullClosure=True)]}, 1)
check("refused: a flooding sentence cannot badge a flooded report shown as a full closure",
      refusedq("fwet", "All of Dairy Road is flooded.", None, P2) and recq("fwet")["verified"] is None)
check("a flooding sentence badges a flooded report that is not shown closed",
      not refusedq("dairy", "All of Dairy Road is flooded.", None, P2) and recq("dairy")["verified"]["quote"].endswith("flooded."))
check("a same-post, same-episode closure sentence raises and verifies (positive control)",
      not refusedq("dfull", "All of Dairy Road is closed.") and recq("dfull")["verified"]["publishedDate"] == "2026-11-02")
# The real City of Melbourne notice layout (structural fixture of the Nov 2 notice: #main-content > .col-m-8)
REAL = open(os.path.join(repo, "tests", "fixtures", "melbourne_notice_2026-11-02.html")).read()
for q in ("All of Dairy Road is closed.", "All of Lake Washington Road is closed.",
          "Babcock Street at University Boulevard is closed."):
    check(f"the real notice column dates its own sentence: {q}", (V.post_of(REAL, q) or [None])[0] == "2026-11-02")
check("the real notice's sidebar column is not the notice", V.post_of(REAL, "Sarno Road at Croton Road is closed.") is None)
check("a dated block outside #main-content (footer) is not a notice",
      V.post_of(REAL, "Minton Road at Norfolk Parkway is closed.") is None)
run(pq, {"sources": {"manual": "up"}, "items": [
    man("dairy", road="Dairy Rd", spot="at Florida Ave", where="Dairy Rd at Florida Ave"),
    man("dfull", road="Dairy Rd", spot="at Babcock St", status="closed", fullClosure=True),
    man("fwet", road="Dairy Rd", spot="at Babcock St", status="flooded", fullClosure=True),
    man("bab", road="Babcock St", spot="at University Blvd", status="closed")]}, 2)
check("the real notice verifies the matching Babcock closure, dated Nov 2",
      not refusedq("bab", "Babcock Street at University Boulevard is closed.", None, REAL)
      and recq("bab")["verified"]["publishedDate"] == "2026-11-02")
for para in ("This claim is false: All of Dairy Road is closed.", "Exercise scenario: All of Dairy Road is closed.",
             "All of Dairy Road is closed. This previous notice is no longer in effect."):
    pg = f"<html><body><article><p>Published on November 2, 2026</p><p>{para}</p></article></body></html>"
    check(f"refused, history untouched: the quote is only part of the paragraph '{para}'",
          refusedq("dairy", "All of Dairy Road is closed.", "full", pg) and recq("dairy")["fields"]["status"] == "flooded")
fake = dict(good["evidence"], quote="All of Hollywood Boulevard is closed.")
E.append([{"rid": "brevard:manual:dfull", "src": "manual", "kind": "verified", "actor": "x", "evidence": fake}], pq, now=NOWV)
check("a well-formed raw receipt about another road does not badge this record",
      recq("dfull")["verified"]["quote"] == "All of Dairy Road is closed.")
st = E.derive(E.load(p))
feed = [i for i in R.display(st)[0] if i["id"] == "brevard:fl511:3"][0]
check("an FL511 feed record is government verified as listed by FL511",
      feed["verified"]["kind"] == "feed" and feed["verified"]["agency"] == "FL511 (FDOT)")
gone = R.display(E.derive(E.load(p2)))[0][0]
check("an FL511 record the feed dropped says no longer listed as closed, still active, cited",
      gone["noLongerListed"]["wasClosed"] and gone["noLongerListed"]["url"] == "https://fl511.com" and gone["awaitingOfficial"])
check("a listed record has no no-longer-listed note", "noLongerListed" not in feed)
pr = os.path.join(tmp, "raise.jsonl")
run(pr, {"sources": {"fl511": "up"}, "items": [item(9, status="caution", fullClosure=False, updated="2026-11-02T11:00:00Z")]}, 0)
run(pr, {"sources": {"fl511": "up"}, "items": [item(9, updated="2026-11-02T15:00:00Z")]}, 1)
fv = [i for i in R.display(E.derive(E.load(pr)))[0] if i["id"] == "brevard:fl511:9"][0]
check("a feed badge is dated by the observation that made it a closure, not the first caution",
      fv["verified"]["publishedAt"] == "2026-11-02T15:00:00Z")

# NWS Melbourne Local Storm Reports: Brevard flood entries only, intersection from the remark, closure only when said
LSR = """..TIME...   ...EVENT...      ...CITY LOCATION...     ...LAT.LON...
..DATE...   ....MAG....      ..COUNTY LOCATION..ST.. ...SOURCE....
            ..REMARKS..

0851 AM     Flood            West Melbourne          28.08N 80.67W
11/02/2026                   Brevard            FL   Broadcast Media

            Media reports a road closure at at Helen St
            & Minton Rd due to flooding.

0706 AM     Flood            3 NE Palm Bay           28.01N 80.63W
11/02/2026                   Brevard            FL   Trained Spotter

            Trained spotter reported flooding near
            Nebraska St NE & Rome Ave NE. Two vehicles
            are stalled in the water.

0400 AM     Flood            3 W West Melbourne      28.07N 80.71W
11/02/2026                   Brevard            FL   NWS Employee

            Horizon View Ave is flooded.

1152 PM     Tstm Wnd Gst     3 SE Floridana Beach    27.90N 80.47W
11/01/2026  M43 MPH          Brevard            FL   Mesonet

1136 AM     Flash Flood      1 W Okeechobee          27.24N 80.85W
11/01/2026                   Okeechobee         FL   Law Enforcement

            Road is not passable.

&&
"""
ents = R.lsr_entries(LSR)
check("storm reports: only Brevard flood entries", [e["source"] for e in ents] == ["Broadcast Media", "Trained Spotter", "NWS Employee"])
its = [R.lsr_item(e, "2026-11-02T13:51:00Z", "https://api.weather.gov/products/x") for e in ents]
check("storm report: a reported road closure shows closed, at the remark's intersection",
      (its[0]["status"], its[0]["road"], its[0]["spot"]) == ("closed", "Helen St", "at Minton Rd") and not its[0]["fullClosure"])
check("storm report: flooding stays flooded; the cross street stops at the sentence end",
      (its[1]["status"], its[1]["road"], its[1]["spot"]) == ("flooded", "Nebraska St NE", "at Rome Ave NE"))
check("storm report: 'X is flooded' names the road; the point is approximate",
      its[2]["road"] == "Horizon View Ave" and its[2]["city"] == "West Melbourne" and its[2]["approx"])
check("storm report: a negated closure remark does not close the road",
      R.lsr_item(dict(ents[0], remarks="Road closure at Helen St & Minton Rd is no longer in effect."), "x", "u")["status"] == "flooded")
for neg in ("No road closed at Helen St & Minton Rd.", "A road closure is possible at Helen St & Minton Rd.",
            "Road closure at Helen St & Minton Rd has ended.", "Road closure at Helen St & Minton Rd is no longer in effect.",
            "Police deny a road closure at Helen St & Minton Rd.", "Road closure at Helen St & Minton Rd is canceled."):
    check(f"storm report: not closed for '{neg}'", R.lsr_item(dict(ents[0], remarks=neg), "x", "u")["status"] == "flooded")
multi = R.lsr_item(dict(ents[0], remarks="Flooding near Palm Bay Rd & Babcock St. Police report a road closure at "
                                         "Helen St & Minton Rd due to flooding."), "x", "u")
mixed = R.lsr_item(dict(ents[0], remarks="Flooding near Palm Bay Rd & Babcock St, with a road closure at Helen St & "
                                         "Minton Rd."), "x", "u")
check("storm report: a closure clause inside a flooding sentence does not close the flooded intersection",
      mixed["status"] == "flooded" and mixed["road"] == "Palm Bay Rd")
check("storm report: in a multi-location remark the closure binds to its own intersection",
      (multi["status"], multi["road"], multi["spot"]) == ("closed", "Helen St", "at Minton Rd"))
henry = R.lsr_item(dict(ents[0], remarks="Media reports a road closure at Henry Ave & Minton Rd due to flooding."), "x", "u")
check("two places reported in the same minute at the same rounded point keep separate ids",
      henry["id"] != R.lsr_item(ents[0], "x", "u")["id"])
check("an identical repeat (correction or summary) keeps the same id",
      R.lsr_item(dict(ents[0]), "x", "u")["id"] == R.lsr_item(ents[0], "y", "v")["id"])
check("storm report ids are stable and separate", len({R.rid_of(i) for i in its}) == 3
      and R.rid_of(its[0]).startswith("brevard:nws:") and R.src_of(its[0]) == "nws")
pn = os.path.join(tmp, "nws.jsonl")
run(pn, {"sources": {"nws": "up"}, "items": its}, 0)
run(pn, {"sources": {"nws": "up"}, "items": its[:1]}, 1)
shownn = {i["id"]: i for i in R.display(E.derive(E.load(pn)))[0]}
check("a storm report that ages off the NWS list stays on the map with no 'no longer listed' note",
      len(shownn) == 3 and all("noLongerListed" not in i for i in shownn.values()))
check("a storm report is shown as reported (not government verified), naming who reported it",
      shownn[R.rid_of(its[1])]["verified"] is None and "Trained Spotter" in shownn[R.rid_of(its[1])]["source"])

# One pin per place (Mike, Nov 3): an NWS report at a pin's intersection joins that pin, listed with its reporter
pm = os.path.join(tmp, "merge.jsonl")
helen = R.lsr_item(ents[0], "2026-11-02T13:51:00Z", "https://api.weather.gov/products/x")
check("storm report carries a where label with its cross street", helen["where"] == "Helen St at Minton Rd")
run(pm, {"sources": {"manual": "up", "nws": "up"}, "items": [
    man("minton-helen", road="Minton Rd", dir="NB", spot="at Helen St", where="Minton Rd NB at Helen St"),
    man("minton-192", road="Minton Rd", spot="at US 192 (W New Haven Ave)"), helen, its[1]]}, 0)
shownm = {i["id"]: i for i in R.display(E.derive(E.load(pm)))[0]}
host = shownm["brevard:manual:minton-helen"]
check("an NWS report at the same intersection (either road order) joins the existing pin, no second pin",
      R.rid_of(helen) not in shownm and len(host["nws"]) == 1 and host["nws"][0]["reporter"] == "Broadcast Media")
check("the joined pin shows the more serious report (closed), saying which report set it, history unchanged",
      host["status"] == "closed" and host["statusBy"]["reporter"] == "Broadcast Media" and host["baseStatus"] == "flooded"
      and E.derive(E.load(pm))["records"]["brevard:manual:minton-helen"]["fields"]["status"] == "flooded")
pw = os.path.join(tmp, "mergew.jsonl")
flood_later = R.lsr_item(dict(ents[0], time="11/02/2026 0951 AM", remarks="Flooding near Helen St & Minton Rd."),
                         "2026-11-02T14:51:00Z", "https://api.weather.gov/products/y")
run(pw, {"sources": {"manual": "up", "nws": "up"}, "items": [
    man("mh", road="", where="Minton Rd NB at Helen St"), helen, flood_later]}, 0)
sw = {i["id"]: i for i in R.display(E.derive(E.load(pw)))[0]}
check("a pin known only by its where label still absorbs NWS reports there (no duplicate)", len(sw) == 1)
hw = next(iter(sw.values()))
check("the closed status cites the closing NWS report, not the newer flood-only one",
      hw["status"] == "closed" and hw["statusBy"]["url"].endswith("/x") and hw["nws"][0]["url"].endswith("/y"))
tpl = open(os.path.join(repo, "template.html")).read()
check("a raised pin's own government badge describes its own status, and the short label keeps NWS",
      "status:it.baseStatus" in tpl and "n+`<span class=\"vbs ok\">✓ Government verified" in tpl)
check("a different intersection on the same road is not merged", "nws" not in shownm["brevard:manual:minton-192"])
check("an NWS report with no matching pin is its own pin, carrying its own report",
      shownm[R.rid_of(its[1])]["nws"][0]["reporter"] == "Trained Spotter")
check("NWS reports from the media or a spotter are not marked government; police and emergency managers are",
      not host["nws"][0]["gov"] and not shownm[R.rid_of(its[1])]["nws"][0]["gov"]
      and R.merge_nws([dict(R.lsr_item(dict(ents[0], source="Law Enforcement"), "x", "u"), id="brevard:nws:z")])[0]["nws"][0]["gov"])
for src, gov in (("Law Enforcement", True), ("Emergency Mngr", True), ("Emergency Manager", True), ("NWS Employee", True),
                 ("Official NWS Obs", True), ("NWS Storm Survey", True), ("Other Federal", True),
                 ("Other Federal Agency", True), ("Local Official", True), ("County Official", True),
                 ("Fire Dept/Rescue", False), ("Broadcast Media", False), ("Public", False), ("Trained Spotter", False),
                 ("Amateur Radio", False), ("Social Media", False), ("Unknown", False), ("", False)):
    check(f"NWS source '{src}' government: {gov}", R.nws_gov(src) == gov)
mixed_host = man("mx", road="Helen St", spot="at Minton Rd")
older_media = R.lsr_item(ents[0], "2026-11-02T13:51:00Z", "https://api.weather.gov/products/media")
newer_police = R.lsr_item(dict(ents[0], time="11/02/2026 1051 AM", source="Law Enforcement",
                               remarks="Police report flooding near Helen St & Minton Rd."),
                          "2026-11-02T15:51:00Z", "https://api.weather.gov/products/police")
pmx = os.path.join(tmp, "mixed.jsonl")
run(pmx, {"sources": {"manual": "up", "nws": "up"}, "items": [mixed_host, older_media, newer_police]}, 0)
mx = R.display(E.derive(E.load(pmx)))[0][0]
check("mixed: the closure stays attributed to the media report; the government report is flooding, both cited",
      mx["status"] == "closed" and not mx["statusBy"]["gov"] and mx["statusBy"]["url"].endswith("media")
      and [r["gov"] for r in mx["nws"]] == [True, False] and mx["nws"][0]["status"] == "flooded")
tpl = open(os.path.join(repo, "template.html")).read()
check("the page's government NWS badge names what the government reporter said (flooding or road closure)",
      "'Government verified '+esc(gw)" in tpl and "g.status==='closed'?'road closure':'flooding'" in tpl)

# Mike (Nov 3), for now: an FHP crash the FHP page stops listing is cleared on the next successful fetch, cited
def runc(path, obs, hours):
    now = T0 + datetime.timedelta(hours=hours)
    new, _ = R.plan(obs, E.derive(E.load(path), now), E.iso(now), fhp_clears=True)
    E.append(new, path, clearance_enabled=True, now=now)
    return E.derive(E.load(path), now)
crash = {"id": "fhp-1", "layer": "FHP", "status": "mvc", "road": "I-95", "spot": "at Palm Bay Rd",
         "reported": "2026-11-02T13:00:00Z", "updated": "2026-11-02T13:00:00Z", "title": "Crash at I-95"}
other = dict(crash, id="fhp-2", spot="at Malabar Rd", road="I-95")
fl = item(7)
crid = R.rid_of(crash)
pc = os.path.join(tmp, "clear.jsonl")
runc(pc, {"sources": {"fhp": "up", "fl511": "up"}, "items": [crash, other, fl]}, 0)
s1 = runc(pc, {"sources": {"fhp": "up", "fl511": "up"}, "items": [other]}, 0.25)
check("FHP drop: first miss only flags it (missing), still active",
      s1["records"][crid]["status"] == "active" and s1["records"][crid]["missingSince"])
s2 = runc(pc, {"sources": {"fhp": "down", "fl511": "up"}, "items": [other]}, 0.5)
check("FHP drop: no clearing while the FHP page is down", s2["records"][crid]["status"] == "active")
s3 = runc(pc, {"sources": {"fhp": "up", "fl511": "up"}, "items": [other]}, 0.75)
ev = s3["records"][crid]["evidence"] or {}
check("FHP drop: still gone on the next successful fetch -> cleared, citing the FHP page and both times",
      s3["records"][crid]["status"] == "reopened" and ev.get("kind") == "fhp-dropped" and ev.get("url") == E.FHP_URL
      and ev.get("missingSince") == s1["records"][crid]["missingSince"] and ev.get("reopenedAt"))
check("FL511 dropping a closure never clears it", s3["records"]["brevard:fl511:7"]["status"] == "active")
items3, cleared3 = R.display(s3)
check("a cleared FHP crash leaves the map and is listed as cleared, with its evidence",
      crid not in {i["id"] for i in items3} and cleared3 and cleared3[0]["evidence"]["kind"] == "fhp-dropped")
check("a cleared crash with no other report at its place is not marked still reported",
      cleared3[0]["stillReported"] == [])
road = lambda **k: dict({"title": "x", "status": "closed"}, **k)
check("same spot by name, either order and with suffixes spelled differently",
      R.same_spot(road(where="Norfolk Pkwy at Minton Rd", road="NORFOLK PKWY", lat=28.0434, lng=-80.6715),
                  road(where="Minton Rd at Norfolk Pkwy", road="Minton Rd", lat=28.05, lng=-80.686)))
check("same spot by name with direction and mile-marker tags written differently",
      R.same_spot(road(where="N Wickham Rd at Pineda Cswy", road="N WICKHAM RD"),
                  road(where="Wickham Rd SB at Pineda Causeway", road="Wickham Rd", spot="at Pineda Causeway"))
      and R.same_spot(road(where="I-95 Sb [MM222] at Mm222", road="I-95 SB [MM222]"),
                      road(where="I-95 SB at MM 222", road="I-95", spot="at MM 222")))
check("nearness alone never makes two differently named intersections one place",
      not R.same_spot(road(where="First Rd at Second St", lat=28.10, lng=-80.67),
                      road(where="Other Rd at Fourth St", lat=28.101, lng=-80.67)))
check("opposite directions at the same mile marker are different places",
      not R.same_spot(road(where="I-95 NB at MM 222", road="I-95 NB"), road(where="I-95 SB [MM222] at Mm222", road="I-95 SB [MM222]")))
check("not the same spot without a point or a cross street", not R.same_spot(road(road="US-1"), road(road="US-1")))
fhp_closed = dict(crash, id="fhp-9", status="closed", road="Minton Rd", spot="at Norfolk Pkwy",
                  reported="2026-11-02T13:05:00Z", lat=28.0434, lng=-80.6715)
manual = {"id": "m-1", "layer": "manual", "status": "closed", "road": "Norfolk Pkwy", "where": "Norfolk Pkwy at Minton Rd",
          "reported": "2026-11-02T13:10:00Z", "updated": "2026-11-02T13:10:00Z", "title": "Norfolk closed", "source": "WESH 2",
          "lat": 28.05, "lng": -80.686}
pk = os.path.join(tmp, "keep.jsonl")
runc(pk, {"sources": {"fhp": "up", "manual": "up"}, "items": [fhp_closed, manual]}, 0)
runc(pk, {"sources": {"fhp": "up", "manual": "up"}, "items": [manual]}, 0.25)
sk = runc(pk, {"sources": {"fhp": "up", "manual": "up"}, "items": [manual]}, 0.5)
ik, ck = R.display(sk)
check("FHP clears its own closure, but another source's report at that place stays active and is named on the cleared record",
      [i["id"] for i in ik] == [R.rid_of(manual)] and ck[0]["id"] == R.rid_of(fhp_closed)
      and [a["id"] for a in ck[0]["stillReported"]] == [R.rid_of(manual)] and ck[0]["stillReported"][0]["source"] == "WESH 2")
tplc = open(os.path.join(repo, "template.html")).read()
check("the page draws a green pin only for a cleared former closure that nothing else still reports",
      "const greenPin=c=>formerClosure(c)&&c.lat!=null&&!(c.stillReported||[]).length" in tplc
      and "isClosed(c)" in tplc and "no longer lists this as a road closure" in tplc)
check("the road search filters the cleared list and the green pins",
      "cleared.filter(c=>greenPin(c)&&matchQ(c,q))" in tplc and "const hit=cleared.filter(c=>matchQ(c,q))" in tplc
      and "No active reports match that search." in tplc)
check("the page ends the 24-hour window itself: expired entries are filtered on load and removed at their deadline",
      "let cleared=allCleared.filter(live)" in tplc and "Date.now()-new Date(c.reopenedAt)<DAY" in tplc
      and "cleared=allCleared.filter(live); draw(); expire();" in tplc)
check("an active report at a cleared FHP closure's place notes FHP's drop and says it stays", "${fhpNote(it)}" in tplc
      and "stays until a cited source clears it" in tplc)
check("check_events accepts the FHP-drop clearance with clearance enabled", subprocess.run(
    [sys.executable, "-I", os.path.join(repo, "check_events.py"), "--before", "", "--bootstrap", "--events", pc],
    capture_output=True).returncode == 0)
s4 = runc(pc, {"sources": {"fhp": "up", "fl511": "up"}, "items": [crash, other]}, 1)
check("the same FHP crash listed again after clearing is active again", s4["records"][crid]["status"] == "active")
county_off = os.path.join(tmp, "county_off.json")
cj = json.load(open(os.path.join(repo, "county.json"))); cj["clearance"]["enabled"] = False
json.dump(cj, open(county_off, "w"))
check("switching the rule off keeps the accepted history (with its clearances) valid", subprocess.run(
    [sys.executable, "-I", os.path.join(repo, "check_events.py"), "--before", pc, "--events", pc, "--county", county_off],
    capture_output=True).returncode == 0)
check("reconcile only proposes FHP clears when clearance is enabled AND the kind is listed",
      'bool(clearance.get("enabled")) and "fhp-dropped" in clearance.get("kinds", [])'
      in open(os.path.join(repo, "reconcile.py")).read())
s5 = runc(pc, {"sources": {"fhp": "up", "fl511": "up"}, "items": [crash, other]}, 1.25)
new5, _ = R.plan({"sources": {"fhp": "up"}, "items": [other]}, s5, E.iso(T0 + datetime.timedelta(hours=1.5)))
check("with clearing off, a dropped FHP incident is only flagged", all(e["kind"] != "cleared" for e in new5))
for bad, why in (({"kind": "fhp-dropped", "agency": "Florida Highway Patrol", "url": E.FHP_URL,
                   "missingSince": "2026-11-02T12:00:00Z", "reopenedAt": ""}, "an FL511 record"),
                 ({"kind": "official", "url": "https://example.com", "reopenedAt": ""}, "another clearance kind")):
    try:
        E.append([{"rid": "brevard:fl511:7", "src": "fl511", "kind": "cleared", "actor": "x", "evidence": bad}], pc,
                 clearance_enabled=True)
        ok = E.derive(E.load(pc))["records"]["brevard:fl511:7"]["status"] == "active"
    except E.Refused:
        ok = True
    check(f"no clearance for {why}", ok)

# FHP ids are the same in separate processes (the old id used Python's per-process hash)
code = ("import sys; sys.path.insert(0, sys.argv[1]); import reconcile as R; "
        "print(R.rid_of({'layer': 'FHP', 'id': 'fhp-x', 'reported': '2026-11-02T14:14:28Z', "
        "'road': 'W King St', 'spot': 'at Clearlake Rd'}))")
ids = {subprocess.run([sys.executable, "-c", code, repo], capture_output=True, text=True,
                      env={"PYTHONHASHSEED": str(n)}).stdout.strip() for n in (1, 2, 3)}
check("FHP record id is stable across processes", len(ids) == 1 and next(iter(ids)).startswith("brevard:fhp:2026"))

# Eastern time across daylight saving (the old code used a fixed EST offset)
src = open(os.path.join(repo, "update_flood_data.py")).read()
tree = ast.parse(src)
keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "TZ" for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name in ("iso", "parse_fhp_time", "parse_fl511_time"))]
ns = {}
exec(compile(ast.Module(body=keep, type_ignores=[]), "update_flood_data.py", "exec"), ns)
check("FHP time in summer (EDT) converts with -4 h", ns["parse_fhp_time"]("07/04/2026 08:00:00") == "2026-07-04T12:00:00Z")
check("FHP time in winter (EST) converts with -5 h", ns["parse_fhp_time"]("11/03/2026 08:00:00") == "2026-11-03T13:00:00Z")
check("FL511 time converts with daylight saving", ns["parse_fl511_time"]("7/4/26, 8:00 AM") == "2026-07-04T12:00:00Z")

print("failures:", fails)
sys.exit(1 if fails else 0)
