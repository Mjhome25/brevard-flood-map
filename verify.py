#!/usr/bin/env python3
"""Mark a report Government verified from a government web page (badge only; never changes status or clears).

  python3 verify.py <rid> <url> "<exact quote from the page>" <published YYYY-MM-DD>

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


def main(argv) -> int:
    rid, url, quote, published = argv
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
    w = E.append([ev])
    print(f"verify: {'wrote' if w else 'already verified, nothing written'} {rid} <- {hosts[host]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
