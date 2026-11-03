#!/usr/bin/env python3
"""Mark a report Government verified from a government web page (a badge; never clears anything).

  python3 verify.py <rid> <https url> "<exact sentence from the page>" [--closed | --full-closure]

The page is fetched here with an honest User-Agent; the request and any redirect must stay on one exact https host
from the county's verify list. The sentence must appear on the page and be exactly one of the supported forms, with this
report's road as its subject:
  "All of <road> is closed."   "<road> at <cross street> is closed."
  "<road> at <cross street> is flooded."   "Flooding on <road> at <cross street>."
Anything else (other wording, extra clauses, names, hedges) is refused and stays unverified, so only road wording is
ever stored. A closure sentence verifies a closure; a flooding sentence only a flood report. The date is the
"Published on <Month D, YYYY>" of the post holding the sentence, never a date typed here, and it must not be older
than this report (an old post is another event).

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
VERSION = E.VERIFIERS[-1]
PUBLISHED = re.compile(r"Published on (January|February|March|April|May|June|July|August|September|October|"
                       r"November|December) (\d{1,2}), (\d{4})")


def page_text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page,
                                                     flags=re.S | re.I))).strip()


def post_date(text: str, at: int) -> str | None:
    """The date of the post holding the sentence: the nearest 'Published on' before it, not one elsewhere."""
    m = None
    for m in PUBLISHED.finditer(text, 0, at):
        pass
    if not m:
        return None
    return datetime.datetime.strptime(" ".join(m.groups()), "%B %d %Y").strftime("%Y-%m-%d")


class SameHost(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to https on the same host; refuse before requesting anything else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        n = urllib.parse.urlparse(newurl)
        if n.scheme != "https" or n.hostname != urllib.parse.urlparse(req.full_url).hostname or n.port or n.username:
            raise E.Refused(f"redirect to {newurl} leaves the government host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def issue(rid: str, url: str, quote: str, raise_to: str | None, page: str, final_url: str,
          now: datetime.datetime | None = None, path: str = E.EVENTS) -> list[dict]:
    """Check the fetched page and write the verified (and raising) events. Raises E.Refused with the reason."""
    now = now or E.utcnow()
    hosts = E.verify_hosts()
    p, f = urllib.parse.urlparse(url), urllib.parse.urlparse(final_url)
    if (p.scheme != "https" or p.hostname not in hosts or f.scheme != "https" or f.hostname != p.hostname
            or p.port or f.port or p.username or f.username):
        raise E.Refused(f"{url} (final {final_url}) is not https on one government host from the county's verify list")
    rec = E.derive(E.load(path), now)["records"].get(rid)
    if not rec or rec["status"] != "active":
        raise E.Refused(f"no active record {rid}")
    text = page_text(page)
    if quote not in text:
        raise E.Refused("the sentence is not on the page")
    published = post_date(text, text.index(quote))
    if not published or published > E.iso(now)[:10]:
        raise E.Refused("the sentence's post has no usable 'Published on' date")
    if published < E._episode(rec):
        raise E.Refused(f"the post ({published}) is older than this report ({E._episode(rec)}), so it is another event")
    why = E.supports(rec["fields"], quote, raise_to)
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
        opener = urllib.request.build_opener(SameHost)
        resp = opener.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20)
        w = issue(rid, url, quote, raise_to, resp.read().decode("utf-8", "replace"), resp.geturl())
    except E.Refused as exc:
        sys.exit(f"verify: {exc}; nothing written")
    print(f"verify: wrote {len(w)} event(s) for {rid}" if w else f"verify: nothing new for {rid}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
