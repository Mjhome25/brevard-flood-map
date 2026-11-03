#!/usr/bin/env python3
"""Mark a report Government verified from a government web page (a badge; never clears anything).

  python3 verify.py <rid> <https url> "<exact sentence from the page>" [--closed | --full-closure]

The page is fetched here with an honest User-Agent; the request and any redirect must stay on one exact https host
from the county's verify list. The sentence must appear on the page and must be about THIS report: it names the
record's road, and its cross street unless it covers the whole road ("All of Dairy Road ..."), and it states the
same impact (a closure sentence verifies a closure, a flooding sentence a flood report). Negated, reopening, partial,
future or conditional sentences are refused, and so is any personal detail. The date is the page's own
"Published on <Month D, YYYY>", never a date typed here.

--closed / --full-closure: the sentence says the road is closed (all of it, for --full-closure), so the report is
first raised to closed, citing the page (Mike, Nov 3: "if the government page says a road is closed, we should go by
what the government page says and cite it"). It only raises.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # also under python3 -I
import events as E  # noqa: E402

UA = "BrevardEmergencyMap/1.0 (+https://brevardemergency.com; public road-closure map)"
VERSION = "verify.py/2"
SUFFIX = {"st": "street", "rd": "road", "ave": "avenue", "blvd": "boulevard", "dr": "drive", "pkwy": "parkway",
          "ln": "lane", "hwy": "highway", "ct": "court", "pl": "place", "cswy": "causeway"}
CLOSED = re.compile(r"\b(?:is|are)\s+closed\b|\bclosed\b", re.I)
FLOOD = re.compile(r"\bflood\w*|\bunder water\b|\bwater over\b|\bhigh water\b", re.I)
DOUBT = re.compile(r"\b(?:not|no longer|re-?open\w*|open|partial\w*|lanes?|shoulder|will|may|might|expected|"
                   r"until|possible|possibly|could|plans?|scheduled|tomorrow)\b", re.I)
PUBLISHED = re.compile(r"Published on (January|February|March|April|May|June|July|August|September|October|"
                       r"November|December) (\d{1,2}), (\d{4})")


def words(s: str) -> str:
    """Lower-case road wording with suffixes spelled out: 'Babcock St' -> 'babcock street'."""
    out = [SUFFIX.get(w, w) for w in re.findall(r"[a-z0-9]+", (s or "").lower())]
    return " ".join(out)


def page_text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page,
                                                     flags=re.S | re.I))).strip()


def page_date(text: str) -> str | None:
    m = PUBLISHED.search(text)
    if not m:
        return None
    return datetime.datetime.strptime(" ".join(m.groups()), "%B %d %Y").strftime("%Y-%m-%d")


def check_claim(fields: dict, quote: str, raise_to: str | None) -> str | None:
    """None when the sentence supports this report's road and impact; otherwise the reason it does not."""
    q = words(quote)
    if DOUBT.search(quote):
        return "the sentence is negated, partial, future or conditional"
    if E.SENSITIVE.search(quote) or len(quote) > 200:
        return "the sentence carries personal detail or is too long"
    road = words(re.split(r"\s*\(|/", fields.get("roadShort") or fields.get("road") or "")[0])
    if not road or road not in q:
        return f"the sentence does not name this road ({road or 'unknown'})"
    whole = re.search(r"\b(?:all of|entire|entirety of)\b", q)
    spot = fields.get("spot") or (fields.get("where") or "").partition(" at ")[2]
    cross = words(re.sub(r"^(?:at|near|from)\s+", "", spot, flags=re.I).split(" to ")[0])
    if not whole and (not cross or cross not in q):
        return f"the sentence does not name this report's cross street ({cross or 'none recorded'})"
    closed = bool(CLOSED.search(quote))
    status = "closed" if raise_to else fields.get("status")
    if closed and status != "closed":
        return "the sentence says closed but the report is not a closure (use --closed to raise it)"
    if raise_to == "full" and not whole:
        return "--full-closure needs a sentence covering the whole road"
    if not closed and not (FLOOD.search(quote) and status in ("flooded", "closed")):
        return "the sentence does not state this report's impact"
    return None


def issue(rid: str, url: str, quote: str, raise_to: str | None, page: str, final_url: str,
          now: datetime.datetime | None = None, path: str = E.EVENTS) -> list[dict]:
    """Check the fetched page and write the verified (and raising) events. Raises E.Refused with the reason."""
    now = now or E.utcnow()
    hosts = E.verify_hosts()
    p, f = urllib.parse.urlparse(url), urllib.parse.urlparse(final_url)
    if p.scheme != "https" or p.hostname not in hosts or f.scheme != "https" or f.hostname != p.hostname:
        raise E.Refused(f"{url} (final {final_url}) is not https on one government host from the county's verify list")
    rec = E.derive(E.load(path), now)["records"].get(rid)
    if not rec or rec["status"] != "active":
        raise E.Refused(f"no active record {rid}")
    text = page_text(page)
    if quote not in text:
        raise E.Refused("the sentence is not on the page")
    published = page_date(text)
    if not published or published > E.iso(now)[:10]:
        raise E.Refused("the page has no usable 'Published on' date")
    why = check_claim(rec["fields"], quote, raise_to)
    if why:
        raise E.Refused(why)
    ev = {"host": p.hostname, "url": url, "finalUrl": final_url, "agency": hosts[p.hostname], "quote": quote,
          "publishedDate": published, "checkedAt": E.iso(now), "pageSha256": hashlib.sha256(text.encode()).hexdigest(),
          "verifier": VERSION}
    new = []
    if raise_to:
        new.append({"rid": rid, "src": rec["src"], "kind": "relisted", "actor": VERSION, "stime": f"{published}T00:00:00Z",
                    "fields": dict(rec["fields"], status="closed", **({"fullClosure": True} if raise_to == "full" else {})),
                    "raisedBy": ev})
    new.append({"rid": rid, "src": rec["src"], "kind": "verified", "actor": VERSION, "evidence": ev})
    return E.append(new, path, now=now)


def main(argv) -> int:
    flags = [a for a in argv if a.startswith("--")]
    rid, url, quote = [a for a in argv if not a.startswith("--")]
    raise_to = "full" if "--full-closure" in flags else "closed" if "--closed" in flags else None
    host = urllib.parse.urlparse(url).hostname
    if urllib.parse.urlparse(url).scheme != "https" or host not in E.verify_hosts():
        sys.exit(f"verify: {url} is not https on the county's government verify list")
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20)
        w = issue(rid, url, quote, raise_to, resp.read().decode("utf-8", "replace"), resp.geturl())
    except E.Refused as exc:
        sys.exit(f"verify: {exc}; nothing written")
    print(f"verify: wrote {len(w)} event(s) for {rid}" if w else f"verify: nothing new for {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
