#!/usr/bin/env python3
"""Mark a report Government verified from a government web page (badge only; never changes status or clears).

  python3 verify.py <rid> <url> "<exact quote from the page>" <published YYYY-MM-DD> [--closed | --full-closure]

--closed / --full-closure: the quote says the road is closed (all of it, for --full-closure), so the report is
raised to closed, citing the page (Mike, Nov 3: "go by what the government page says and cite it"). It only raises;
a quote with negation or reopening words is refused.

The page is fetched here with an honest User-Agent and the quote must appear in its text; the host must be on the
county's verify list. Nothing is written from a quote that was not read on the page.
"""
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


CLOSED = re.compile(r"\bclosed\b", re.I)
NOT_CLOSED = re.compile(r"\b(?:not|no longer|re-?opened|open(?:ed)?|partially|lane|shoulder|will be|until)\b", re.I)


def main(argv) -> int:
    flags = [a for a in argv if a.startswith("--")]
    rid, url, quote, published = [a for a in argv if not a.startswith("--")]
    raise_to = "full" if "--full-closure" in flags else "closed" if "--closed" in flags else None
    if raise_to and not (CLOSED.search(quote) and not NOT_CLOSED.search(quote)):
        sys.exit("verify: the quote does not plainly say the road is closed; nothing written")
    host = urllib.parse.urlparse(url).hostname
    hosts = E.verify_hosts()
    if host not in hosts:
        sys.exit(f"verify: {host} is not on the county's government verify list")
    if rid not in E.derive(E.load())["records"]:
        sys.exit(f"verify: no record {rid}")
    page = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20).read().decode("utf-8", "replace")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page, flags=re.S)))
    if quote not in text:
        sys.exit("verify: the quote is not on the page; nothing written")
    ev = {"rid": rid, "src": rid.split(":")[1], "kind": "verified", "actor": "verify.py",
          "evidence": {"host": host, "url": url, "agency": hosts[host], "quote": quote,
                       "publishedAt": f"{published}T00:00:00Z", "checkedAt": E.iso(E.utcnow()),
                       "pageSha256": hashlib.sha256(text.encode()).hexdigest(), "verifier": "verify.py/1"}}
    new = [ev]
    if raise_to:
        rec = E.derive(E.load())["records"][rid]
        cite = {k: ev["evidence"][k] for k in ("host", "url", "agency", "quote")}
        new.append({"rid": rid, "src": ev["src"], "kind": "relisted", "actor": "verify.py", "stime": ev["evidence"]["publishedAt"],
                    "fields": dict(rec["fields"], status="closed", **({"fullClosure": True} if raise_to == "full" else {})),
                    "raisedBy": cite})
    w = E.append(new)
    print(f"verify: wrote {len(w)} event(s) for {rid} <- {hosts[host]}" if w else f"verify: nothing new for {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
