#!/usr/bin/env python3
"""Refresh the reviewed Melbourne water-system notice; retain it on failed/ambiguous checks.

The county polygon is a utility service boundary, not city limits or a customer-address list.
Only an explicit, dated, full-system rescission can remove this notice. No age-based expiry.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import html.parser
import json
import math
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from verify import Posts, PUBLISHED, SameHost

FILE = HERE / "water_notices.json"
HOST = "www.melbourneflorida.org"
INDEX = "https://" + HOST + "/Government/Departments/Public-Works-Utilities/Precautionary-Boil-Water-Notices"
NOTICE = INDEX + "/Precautionary-Boil-Water-Notice-Issued-for-All-Melbourne-Water-Customers"
UPDATE = INDEX + "/Update-PBWN-Nov-3-0845"
BOUNDARY = ("https://gis.brevardfl.gov/gissrv/rest/services/Utility_Services/"
            "Other_Service_Area_WKID2881/MapServer/5/query?where=1%3D1&outFields=*&outSR=4326&returnGeometry=true&f=geojson")
UA = "BrevardFloodMap/0.1 (+https://brevardemergency.com)"
START = "2026-11-02"
SCOPE = r"all (?:City of )?Melbourne water customers"
ACTIVE = re.compile(r"(?:A|The) precautionary boil water notice has been issued for " + SCOPE
                    + r"(?:, including (?:City of )?West Melbourne water customers)?\.", re.I)
LIFTED = re.compile(r"The precautionary boil water notice (?:for " + SCOPE
                    + r" (?:has been|is)(?: hereby)? (?:rescinded|lifted)|has been (?:rescinded|lifted) for "
                    + SCOPE + r")\.", re.I)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in (HOST, "gis.brevardfl.gov") or p.port or p.username or p.password:
        raise ValueError("unexpected utility source")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.build_opener(SameHost()).open(req, timeout=20) as response:
        return response.read().decode("utf-8")


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        url = urllib.parse.urljoin(INDEX + "/", dict(attrs).get("href", ""))
        p = urllib.parse.urlparse(url)
        if (p.scheme == "https" and p.hostname == HOST and not p.username and not p.port
                and url.startswith(INDEX + "/")):
            if url not in self.urls:
                self.urls.append(url)


def statements(page, url, today, pending=None):
    parser = Posts()
    parser.feed(re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S | re.I))
    flat = lambda parts: re.sub(r"\s+", " ", " ".join(parts).replace("\xa0", " ")).strip()
    found = []
    for i, parts in enumerate(parser.posts):
        count_before = len(found)
        text = flat(parts)
        dates = list(PUBLISHED.finditer(text))
        if len(dates) != 1:
            continue
        day = dt.datetime.strptime(" ".join(dates[0].groups()), "%B %d %Y").date().isoformat()
        if not START <= day <= today:
            continue
        # A source's explicit update time is distinct from our check time. Date-only posts stay date-only.
        tm = re.search(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", text, re.I)
        updated = None
        if tm:
            hour = int(tm[1]) % 12 + (12 if tm[3].lower() == "p" else 0)
            if int(tm[1]) in range(1, 13) and int(tm[2]) < 60:
                updated = iso(dt.datetime.fromisoformat(day).replace(hour=hour, minute=int(tm[2]),
                                                                   tzinfo=ZoneInfo("America/New_York")))
        for post, unit in parser.units:
            quote = flat(unit)
            if post != i or text.index(quote) < dates[0].end():
                continue
            status = "active" if ACTIVE.fullmatch(quote) else "rescinded" if LIFTED.fullmatch(quote) else None
            if status == "rescinded" and re.search(r"\b(?:will be (?:rescinded|lifted)|effective (?:tomorrow|on)|scheduled to|exercise|scenario|not (?:rescinded|lifted))\b", text, re.I):
                status = None  # contradictory, future or exercise context needs a human check, not automatic removal
            if status:
                found.append({"status": status, "publishedDate": day, "updatedAt": updated, "sourceUrl": url,
                              "statement": quote, "sourceSha256": hashlib.sha256(text.encode()).hexdigest()})
        if (pending is not None and len(found) == count_before and re.search(SCOPE, text, re.I)
                and re.search(r"\b(?:rescission|rescinded|lifted)\b", text, re.I)):
            pending.append(url)
    return found


def geometry(raw):
    data = json.loads(raw)
    features = data.get("features", [])
    if data.get("type") != "FeatureCollection" or len(features) != 1 or data.get("exceededTransferLimit"):
        raise ValueError("unexpected service-area response")
    geom = features[0].get("geometry") or {}
    if geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("service area is not a polygon")
    polys = [geom.get("coordinates")] if geom["type"] == "Polygon" else geom.get("coordinates")
    points = 0
    if not polys:
        raise ValueError("empty service area")
    for polygon in polys:
        if not polygon:
            raise ValueError("empty service polygon")
        for ring in polygon:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("invalid service-area ring")
            for point in ring:
                if (len(point) < 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                                          for x in point[:2]) or not (-81.1 <= point[0] <= -80.4 and 27.8 <= point[1] <= 28.8)):
                    raise ValueError("service-area point outside expected county bounds")
                points += 1
    if not 4 <= points <= 20000:
        raise ValueError("unexpected service-area size")
    return geom


def refresh(previous, get=fetch, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    result = copy.deepcopy(previous)
    result.update(schema=1, generated=iso(now), errors=[])
    notices = result.setdefault("notices", [])
    notice = next((n for n in notices if n.get("id") == "melbourne-water-system"), None)
    if notice is None:
        notice = {"id": "melbourne-water-system", "agency": "City of Melbourne", "status": "unknown",
                  "title": "Precautionary boil water notice", "area": "Melbourne water service area",
                  "scope": "Applies to Melbourne water customers, including West Melbourne wholesale customers. This is a service-area boundary; some properties inside it may use private wells.",
                  "issuedDate": START, "sourceUrl": NOTICE, "indexUrl": INDEX, "geometrySourceUrl": BOUNDARY}
        notices.append(notice)
    urls = []
    index_ok = False
    try:
        links = Links()
        links.feed(get(INDEX))
        urls = links.urls[:10]  # the official index orders its current notices first
        index_ok = True
    except Exception:
        result["errors"].append("The utility notice index could not be checked.")
    for url in [notice.get("sourceUrl"), UPDATE, NOTICE]:
        if url and url not in urls:
            urls.append(url)
    candidates = []
    for order, url in enumerate(urls):
        try:
            pending = []
            candidates += [dict(s, order=order) for s in statements(get(url), url, iso(now)[:10], pending)]
            if pending:
                result["errors"].append("A full-system utility update needs review; the last verified notice is retained.")
        except Exception:
            result["errors"].append("A utility update could not be checked.")
    if candidates:
        # Prefer a newer dated source, then the order on the utility's own index. Never let a failed index
        # check reactivate a stale issue page after a verified rescission.
        latest = max(candidates, key=lambda s: (s["publishedDate"], -s["order"], s["status"] == "rescinded"))
        if (notice.get("publishedDate") and (latest["publishedDate"] < notice["publishedDate"]
                or ((not index_ok or result["errors"]) and latest["sourceUrl"] != notice.get("sourceUrl")
                    and latest["publishedDate"] == notice["publishedDate"]))):
            latest = None
        old_status = notice.get("status")
        if latest:
            notice.update({k: v for k, v in latest.items() if k != "order"})
            if not result["errors"]:
                notice["checkedAt"] = iso(now)
        if latest and old_status != notice["status"]:
            if old_status == "rescinded" and notice["status"] == "active":
                notice["issuedDate"] = latest["publishedDate"]
            result.setdefault("history", []).append({"id": notice["id"], "from": old_status, "to": notice["status"],
                                                     "detectedAt": iso(now), "sourceUrl": latest["sourceUrl"],
                                                     "publishedDate": latest["publishedDate"], "statement": latest["statement"],
                                                     "sourceSha256": latest["sourceSha256"]})
    else:
        result["errors"].append("No matching dated full-system statement was read; the last verified notice is retained.")
    if not notice.get("geometry") or (notice.get("geometryCheckedAt") or "")[:10] != iso(now)[:10]:
        try:
            notice["geometry"] = geometry(get(BOUNDARY))
            notice["geometryCheckedAt"] = iso(now)
        except Exception:
            result["errors"].append("The service-area boundary could not be refreshed.")
    result["errors"] = list(dict.fromkeys(result["errors"]))
    return result


def main():
    previous = json.loads(FILE.read_text()) if FILE.exists() else {}
    data = refresh(previous)
    temp = FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n")
    temp.replace(FILE)
    print("water notices:", [(n["id"], n["status"]) for n in data["notices"]], "; source checks:", "partial" if data["errors"] else "ok")


if __name__ == "__main__":
    main()
