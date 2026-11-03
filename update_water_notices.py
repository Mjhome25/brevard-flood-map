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
UPDATE_HEADING = re.compile(r"([A-Za-z]+)\.? (\d{1,2}),? (\d{4}),? (\d{1,2}):(\d{2}) ?([ap])\.?\s?m\.? UPDATE", re.I)


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


class WaterPosts(Posts):
    """Keep the reviewed post scope while also collecting whole update headings."""
    def handle_starttag(self, tag, attrs):
        super().handle_starttag("p" if tag in ("h1", "h2", "h3", "h4") else tag, attrs)

    def handle_endtag(self, tag):
        super().handle_endtag("p" if tag in ("h1", "h2", "h3", "h4") else tag)


def statements(page, url, today, pending=None, now=None):
    parser = WaterPosts()
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
        # Only an entire dated update heading is an update time. A scheduled next update or an old
        # timestamp inside prose is not evidence of when this notice took effect.
        stamps = set()
        future = False
        for post, unit in parser.units:
            tm = UPDATE_HEADING.fullmatch(flat(unit)) if post == i else None
            if not tm:
                continue
            try:
                date = dt.datetime.strptime(f"{tm[1][:3]} {tm[2]} {tm[3]}", "%b %d %Y")
                if date.date().isoformat() != day or not (1 <= int(tm[4]) <= 12 and int(tm[5]) < 60):
                    continue
                hour = int(tm[4]) % 12 + (12 if tm[6].lower() == "p" else 0)
                stamp = date.replace(hour=hour, minute=int(tm[5]), tzinfo=ZoneInfo("America/New_York"))
                if now and stamp > now:
                    future = True
                stamps.add(iso(stamp))
            except ValueError:
                continue
        if future:
            if pending is not None:
                pending.append(url)
            continue
        updated = None
        if len(stamps) == 1:
            updated = next(iter(stamps))
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
        if len({s["status"] for s in found[count_before:]}) > 1:
            # Long update pages can repeat an old issue beneath a new rescission (or the reverse).
            # Without separate dated statement sections, their order is not proof of the current state.
            del found[count_before:]
            if pending is not None:
                pending.append(url)
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
            candidates += [dict(s, order=order) for s in statements(get(url), url, iso(now)[:10], pending, now)]
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


def main(path=None, get=fetch, now=None):
    path = pathlib.Path(path) if path is not None else FILE
    try:
        previous = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(previous, dict) or not isinstance(previous.get("notices", []), list) or any(
                not isinstance(n, dict) for n in previous.get("notices", [])):
            raise ValueError("unsupported water snapshot")
        data = refresh(previous, get, now)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n")
        temp.replace(path)
        print("water notices:", [(n["id"], n["status"]) for n in data["notices"]], "; source checks:", "partial" if data["errors"] else "ok")
    except Exception:
        # Do not overwrite a prior snapshot or stop road reconciliation/publishing. Details stay out of
        # public data; the workflow log explicitly records this source failure.
        print("water notice refresh failed; prior file retained; road update continues", file=sys.stderr)
    return 0


if __name__ == "__main__":
    main()
