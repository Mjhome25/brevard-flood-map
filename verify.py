#!/usr/bin/env python3
"""Mark a report Government verified from a government web page, or reopened when the page says the road is open.

  python3 verify.py <rid> <https url> "<exact sentence from the page>" [--closed | --full-closure | --open]

The page is fetched here with an honest User-Agent; the request and any redirect must stay on one exact https host
from the county's verify list. The sentence must appear on the page and be exactly one of the supported forms, with this
report's road as its subject:
  "All of <road> is closed."   "<road> at <cross street> is closed."
  "<road> at <cross street> is flooded."   "Flooding on <road> at <cross street>."
Anything else (other wording, extra clauses, names, hedges) is refused and stays unverified, so only road wording is
ever stored. A closure sentence verifies a closure; a flooding sentence only a flood report. The date is the
"Published on <Month D, YYYY>" of the post block holding the sentence (an <article>, or the City of Melbourne
notice column), never a date typed here, and it must not be older
than this report (an old post is another event).

--closed / --full-closure: the sentence says the road is closed (all of it, for --full-closure), so the report is
first raised to closed, citing the page (Mike, Nov 3: "if the government page says a road is closed, we should go by
what the government page says and cite it"). It only raises.

--open: the sentence says the road is open, as one of "<road> is open." (optionally ", but there are <...> on the
shoulder"), "All of <road> is open.", "<road> has reopened.", "<road> at <cross street> is open." The record comes
off the active map and shows green for a day, citing the page (Mike, Nov 3: a road may be shown reopened only when an
official statement on a credible website says so). Needs "official" in the county's clearance kinds.
"""
from __future__ import annotations

import datetime
import hashlib
import html
import html.parser
import json
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


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Posts(html.parser.HTMLParser):
    """Text of each supported post block: an <article>, or the City of Melbourne (OpenCities) notice column, a
    div.col-m-8 inside #main-content. Sibling columns, sidebars and navigation are separate blocks or none."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.posts = [], []  # stack of (tag, inside #main-content, index of the post it opens or None)
        self.units = []  # (post index, text) of each <p>/<li> inside a post: a whole notice statement

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        a = dict(attrs)
        main = (self.stack and self.stack[-1][1]) or a.get("id") == "main-content"
        opens = tag == "article" or (tag == "div" and main and "col-m-8" in (a.get("class") or "").split())
        if opens:
            self.posts.append([])
        post = next((s[2] for s in reversed(self.stack) if s[2] is not None), len(self.posts) - 1 if opens else None)
        if tag in ("p", "li") and post is not None:
            self.units.append((post, []))
            self.stack.append((tag, main, None, len(self.units) - 1))
            return
        self.stack.append((tag, main, len(self.posts) - 1 if opens else None, None))

    def handle_endtag(self, tag):
        if tag in VOID or tag not in (s[0] for s in self.stack):
            return  # <img/> and stray closing tags close nothing
        while self.stack:  # entries are (tag, inside #main-content, post index, unit index)
            if self.stack.pop()[0] == tag:
                break

    def handle_data(self, data):
        for _, _, i, u in self.stack:
            if i is not None:
                self.posts[i].append(data)
            if u is not None:
                self.units[u][1].append(data)


def post_of(page: str, quote: str) -> tuple[str, str] | None:
    """(date, post text) of the one supported post block holding the sentence, with its own 'Published on' before it.
    The sentence must be a whole paragraph or list item of that post on its own: no prefix ("This claim is false:"),
    no continuation ("... no longer in effect"). Otherwise, or with no date or several dates, it is not supported."""
    parser = Posts()
    parser.feed(re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I))
    flat = lambda parts: re.sub(r"\s+", " ", " ".join(parts).replace("\xa0", " ")).strip()
    whole = {i for i, parts in parser.units if flat(parts) == quote}
    for i, parts in enumerate(parser.posts):
        text = flat(parts)
        if i not in whole or quote not in text:
            continue
        dates = list(PUBLISHED.finditer(text))
        if len(dates) != 1 or dates[0].start() > text.index(quote):
            return None
        return datetime.datetime.strptime(" ".join(dates[0].groups()), "%B %d %Y").strftime("%Y-%m-%d"), text
    return None


# The update's own heading time only ("Nov. 3, 8:30 a.m. traffic update"): a time directly naming the update. Any other
# time in the post (a scheduled next update, an event time) is never borrowed (Codex #190).
STATED = re.compile(r"(?<![\w:])(\d{1,2}):(\d{2})\s*([ap])\.?\s?m\.?,?\s+(?:(?:traffic|road)\s+)?update\b", re.I)
NOT_NOW = re.compile(r"\b(?:next|following|upcoming|later|scheduled|another)\W+(?:\w+\W+){0,3}$", re.I)


def stated_time(post: str, quote: str, published: str) -> str:
    """The update's own heading time, in Eastern time on the publish day; "" (date-only) when the post names no
    update time before the sentence, or names more than one (ambiguous)."""
    head = post[:post.index(quote)]
    times = set()
    for m in STATED.finditer(head):
        if NOT_NOW.search(head[max(0, m.start() - 40):m.start()]):
            continue
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        if not (1 <= h <= 12 and mi < 60):
            return ""
        times.add((h % 12 + (12 if ap == "p" else 0), mi))
    if len(times) != 1:
        return ""
    (h, mi), = times
    day = datetime.datetime.strptime(published, "%Y-%m-%d")
    return E.iso(day.replace(hour=h, minute=mi, tzinfo=E.ET))


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
    post = post_of(page, quote)
    if not post or post[0] > E.iso(now)[:10]:
        raise E.Refused("the sentence is not inside one dated post (an article or the city's notice column, with its own 'Published on' before it)")
    published = post[0]
    if published < E._episode(rec):
        raise E.Refused(f"the post ({published}) is older than this report ({E._episode(rec)}), so it is another event")
    why = E.opens(rec["fields"], quote) if raise_to == "open" else E.supports(rec["fields"], quote, raise_to)
    if why:
        raise E.Refused(why)
    ev = {"host": p.hostname, "url": url, "finalUrl": final_url, "agency": hosts[p.hostname], "quote": quote,
          "publishedDate": published, "checkedAt": E.iso(now), "pageSha256": hashlib.sha256(text.encode()).hexdigest(),
          "postSha256": hashlib.sha256(post[1].encode()).hexdigest(),
          "verifier": VERSION}
    if raise_to == "open":
        clearance = json.load(open(os.path.join(E.HERE, "county.json"))).get("clearance", {})
        if not (clearance.get("enabled") and "official" in clearance.get("kinds", [])):
            raise E.Refused("official reopenings are not enabled for this county")
        stated = stated_time(post[1], quote, published)
        if not E.official_after({"sourceTime": stated, "publishedDate": published}, rec["lastReported"]):
            raise E.Refused(f"the statement ({stated or published + ', no time given'}) is not newer than this road's "
                            f"latest report ({rec['lastReported']}); a same-day report with a date-only post stays")
        return E.append([{"rid": rid, "src": rec["src"], "kind": "cleared", "actor": VERSION,
                          "evidence": dict(ev, kind="official", sourceTime=stated, reopenedAt="")}], path,
                        clearance_enabled=True, now=now)
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
    raise_to = ("open" if "--open" in flags else "full" if "--full-closure" in flags
                else "closed" if "--closed" in flags else None)
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
