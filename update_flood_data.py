#!/usr/bin/env python3
"""Pull live Brevard County road/flood data and write observations.json (reconcile.py then writes flood_data.json).

Sources (all public, no keys):
  - FL511 list endpoints (Incidents, Closures) filtered to county == Brevard
  - FHP CAD live incident viewer (trafficincidents.flhsmv.gov), Brevard rows
  - NWS active alerts for Brevard County (FLC009) with warning polygons
  - manual_closures.json: hand-curated reports from police/news/Facebook
Run:  python3 update_flood_data.py   (writes flood_data.json next to it)
"""
import json, re, html, os, sys, urllib.request, urllib.parse, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reconcile import fhp_classify, fhp_rows  # noqa: E402
UA = "BrevardFloodMap/0.1 (+https://brevardemergency.com)"
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/New_York")  # FL511 and FHP times are Eastern, across daylight saving

def get(url, timeout=40, accept="application/json", retries=2):
    import time
    last = None
    for i in range(retries + 1):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            return urllib.request.urlopen(r, timeout=timeout).read().decode("utf-8", "replace")
        except Exception as e:
            last = e; time.sleep(2 + 2 * i)
    raise last

def iso(dt): return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_fl511_time(s):  # "11/2/26, 7:43 AM" (Eastern)
    try: return iso(datetime.datetime.strptime(s, "%m/%d/%y, %I:%M %p").replace(tzinfo=TZ))
    except Exception: return None

def parse_fhp_time(s):  # "11/02/2026 06:55:58"
    try: return iso(datetime.datetime.strptime(s, "%m/%d/%Y %H:%M:%S").replace(tzinfo=TZ))
    except Exception: return None

def classify(text):
    t = text.lower()
    if any(k in t for k in ["flood", "water", "high water"]): return "flooded"
    if "all lanes" in t or "road blocked" in t or "roadblock" in t or "closed" in t: return "closed"
    return "caution"

errors = []
items = []

# ---- FL511 ------------------------------------------------------------
def fl511(layer):
    q = urllib.parse.quote(json.dumps({"columns": [], "start": 0, "length": 500}))
    d = json.loads(get(f"https://fl511.com/List/GetData/{layer}?query={q}&lang=en-US"))
    out = []
    for r in d.get("data", []):
        if (r.get("county") or "").strip().lower() != "brevard": continue
        desc = r.get("description") or ""
        out.append({
            "id": f"fl511-{r['id']}", "source": "FL511 (FDOT)", "sourceUrl": "https://fl511.com",
            "road": r.get("roadwayName") or "", "title": desc.split(". Last updated")[0].replace(" in Brevard County", ""),
            "status": "closed" if ("all lanes" in desc.lower() and "flood" not in desc.lower()) else classify(desc),
            "fullClosure": bool(r.get("isFullClosure")) or "all lanes" in desc.lower(),
            "reported": parse_fl511_time(r.get("startDate") or ""), "updated": parse_fl511_time(r.get("lastUpdated") or ""),
            "lat": None, "lng": None, "layer": layer,
            "dir": {"northbound":"NB","southbound":"SB","eastbound":"EB","westbound":"WB","both directions":"both directions"}.get((r.get("direction") or "").strip().lower(), ""),
            "lanes": (r.get("laneDescription") or "").strip(),
            "roadShort": (r.get("roadwayName") or "").split(" / ")[0].strip(),
            "spot": (re.search(r"(?:North|South|East|West)(?:bound)?,\s*(.+?)\.(?:\s|$)", desc) or re.search(r"$^", "")) and re.search(r"(?:North|South|East|West)(?:bound)?,\s*(.+?)\.(?:\s|$)", desc).group(1) or "",
        })
    return out
seen_ids = set()
sources = {}  # per-source health for the closure history: up, down (fetch or parse failed) or empty
for layer in ("Closures", "Incidents"):
    try: items += [it for it in fl511(layer) if not (it["id"] in seen_ids or seen_ids.add(it["id"]))]
    except Exception as e: errors.append(f"FL511 {layer}: {e}"); sources["fl511"] = "down"
if sources.get("fl511") != "down": sources["fl511"] = "up" if any(it["layer"] != "FHP" for it in items) else "empty"

# FL511 list rows carry no coordinates; the map-icon endpoint does (by id). Cache them so a transient failure keeps pins.
cache_path = os.path.join(HERE, "fl511_coords_cache.json")
try: coords = json.load(open(cache_path))
except Exception: coords = {}
icon_errors = []
for layer in ("Closures", "Incidents"):
    try:
        d = json.loads(get(f"https://fl511.com/map/mapIcons/{layer}"))
        for x in d.get("item2", []): coords[str(x["itemId"])] = x["location"]
    except Exception as e: icon_errors.append(f"FL511 map locations {layer}: {e}")
missing = 0
for it in items:
    if it["layer"] in ("Closures", "Incidents"):
        k = it["id"].split("-")[1]
        if k in coords: it["lat"], it["lng"] = coords[k]
        else: missing += 1
if icon_errors: print("note (cache used):", icon_errors)
if missing and icon_errors: errors.append(f"FL511 map locations: {missing} report(s) without a pin")
json.dump(coords, open(cache_path, "w"))

# FL511 detail endpoint gives the affected stretch as an encoded polyline. Cached per id + update time.
def decode_polyline(e):
    pts, i, lat, lng = [], 0, 0, 0
    while i < len(e):
        for k in (0, 1):
            shift = res = 0
            while True:
                b = ord(e[i]) - 63; i += 1; res |= (b & 0x1f) << shift; shift += 5
                if b < 0x20: break
            d = ~(res >> 1) if res & 1 else res >> 1
            if k == 0: lat += d
            else: lng += d
        pts.append([round(lat / 1e5, 6), round(lng / 1e5, 6)])
    return pts
dcache_path = os.path.join(HERE, "fl511_detail_cache.json")
try: dcache = json.load(open(dcache_path))
except Exception: dcache = {}
for it in items:
    if it["layer"] not in ("Closures", "Incidents"): continue
    k = it["id"].split("-")[1]; ck = f"{k}|{it.get('updated')}"
    if ck not in dcache:
        try:
            d = json.loads(get(f"https://fl511.com/map/data/{it['layer']}/{k}", retries=1))
            dcache[ck] = {"line": decode_polyline(d.get("polyline") or ""), "lat": d.get("latitude"), "lng": d.get("longitude")}
        except Exception: continue
    dd = dcache[ck]
    if dd.get("lat") and it["lat"] is None: it["lat"], it["lng"] = dd["lat"], dd["lng"]
    if len(dd.get("line") or []) >= 2: it["line"] = dd["line"]
live = {f"{it['id'].split('-')[1]}|{it.get('updated')}" for it in items if it["layer"] in ("Closures", "Incidents")}
json.dump({k: v for k, v in dcache.items() if k in live}, open(dcache_path, "w"))

# ---- FHP CAD ----------------------------------------------------------
try:
    s = get("https://trafficincidents.flhsmv.gov/SmartWebClient/CadView.aspx", accept="text/html")
    for r in fhp_rows(s):  # bound by header; Received/Dispatched/Arrived, no cleared column
        typ, t_rep, loc, remark, lat, lng = (r["Incident Type"], r["Received"], r["Location"], r["Remarks"],
                                             r["Latitude"], r["Longitude"])
        if typ.upper() == "WEATHER WARNING": continue
        try: lat, lng = float(lat), float(lng)
        except Exception: lat = lng = None
        status, full = fhp_classify(typ, remark)
        items.append({
            "id": f"fhp-{re.sub(r'[^0-9]', '', t_rep)}-{abs(hash(loc)) % 10000}", "source": "FHP live CAD", "sourceUrl": "https://trafficincidents.flhsmv.gov/SmartWebClient/CadView.aspx",
            "road": loc.split(" x[")[0], "title": f"{typ.title()} at {loc}" + (f" ({remark.title()})" if remark else ""),
            "status": status, "fullClosure": full,
            "reported": parse_fhp_time(t_rep), "updated": parse_fhp_time(t_rep), "lat": lat, "lng": lng, "layer": "FHP",
            "roadShort": " ".join(w if re.search(r"\d", w) else w.title() for w in loc.split(" x[")[0].split()), "dir": "",
            "spot": ("at " + m.group(1).title()) if (m := re.search(r"x\[([^\]]+)\]", loc)) else "",
            "lanes": remark.title() if remark else "",
        })
    sources["fhp"] = "up" if any(it["layer"] == "FHP" for it in items) else "empty"
except Exception as e: errors.append(f"FHP CAD: {e}"); sources["fhp"] = "down"

# ---- NWS --------------------------------------------------------------
alerts = []
try:
    d = json.loads(get("https://api.weather.gov/alerts/active?zone=FLC009,FLZ247,FLZ347,FLZ447,FLZ547,FLZ647,FLZ747", accept="application/geo+json"))
    seen = set()
    for f in d.get("features", []):
        p = f["properties"]
        if p["id"] in seen: continue
        seen.add(p["id"])
        alerts.append({"id": p["id"], "event": p["event"], "severity": p.get("severity"), "headline": p.get("headline"),
                       "areas": p.get("areaDesc"), "effective": p.get("effective"), "expires": p.get("expires"),
                       "description": (p.get("description") or "")[:1200], "geometry": f.get("geometry")})
except Exception as e: errors.append(f"NWS: {e}")

# ---- manual reports ---------------------------------------------------
manual = []
try:
    manual = json.load(open(os.path.join(HERE, "manual_closures.json")))
    for m in manual: m.setdefault("layer", "manual")
    sources["manual"] = "up" if manual else "empty"
except Exception as e: errors.append(f"manual_closures.json: {e}"); sources["manual"] = "down"

advisories = []
try: advisories = json.load(open(os.path.join(HERE, "advisories.json")))
except Exception as e: errors.append(f"advisories.json: {e}")
data = {"generated": iso(datetime.datetime.now(datetime.timezone.utc)), "errors": errors,
        "items": manual + items, "alerts": alerts, "advisories": advisories, "sources": sources}
# ---- "where" label: road, direction and the exact spot -----------------
DIRW = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB"}
for it in manual:  # hand-entered reports: split "Minton Rd southbound at US 192" into road / direction / spot
    if it.get("roadShort") or not it.get("road"): continue
    m = re.match(r"(.+?)\s+(northbound|southbound|eastbound|westbound)?\s*\b((?:at|between|near|beyond)\b.*)?$", it["road"], re.I)
    if m:
        it["roadShort"] = m.group(1).strip(" /")
        if m.group(2) and not it.get("dir"): it["dir"] = DIRW[m.group(2).lower()]
        if m.group(3) and not it.get("spot"): it["spot"] = m.group(3).strip()
for it in manual + items:
    if it.get("where"): continue
    road = it.get("roadShort") or it.get("road") or ""
    it["where"] = " ".join(x for x in (road, it.get("dir", ""), it.get("spot", "")) if x).strip() or it.get("title", "")

# ---- closure start/end streets from the county 911 street layer ---------
try:
    import sys; sys.path.insert(0, HERE)
    from closure_extent import add_extents
    stale = lambda it: False  # closures never fade (Mike, November 3, 2026)
    ext_errs = add_extents(manual + items, HERE, lambda it: (it.get("fullClosure") or it.get("status") == "closed") and not stale(it))
    if ext_errs: print("note (closure extents):", ext_errs[:3])
except Exception as e: print("note (closure extents skipped):", e)

# ---- privacy wording filter (Mike, Nov 2 2026) --------------------------
# Wrecks are shown only as "MVC" with the road impact. No fatality, injury or
# personal details from any source ever reach the page.
import re
from events import CRASH as _CRASH, SENSITIVE as _SENSITIVE  # noqa: E402  (shared with evidence checks)
def scrub(t):
    if not t: return t
    t = _CRASH.sub("MVC", t)
    t = re.sub(r"\([^)]*\)", lambda m: "" if _SENSITIVE.search(m.group(0)) else m.group(0), t)
    t = _SENSITIVE.sub("", t)
    t = re.sub(r"\bMVC(?:\W+MVC)+\b", "MVC", t)
    t = re.sub(r"\s*\(MVC\)", "", t)
    t = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", t)).strip(" -,;")
    return t
def scrub_prose(t):
    """Free text: drop any sentence with personal/fatality detail; note an MVC if one was described."""
    if not t: return t
    out, mvc = [], False
    for sent in re.split(r"(?<=[.!?;])\s+", t):
        if _SENSITIVE.search(sent) or re.search(r"\bfatal", sent, re.I):
            mvc = mvc or bool(_CRASH.search(sent)); continue
        out.append(_CRASH.sub("MVC", sent))
    if mvc and not any("MVC" in x for x in out): out.insert(0, "MVC reported.")
    return " ".join(out).strip()
for it in data["items"]:
    for k in ("title", "road", "where", "spot", "lanes", "roadShort", "from", "to"):
        if it.get(k): it[k] = scrub(it[k])
    if it.get("note"): it["note"] = scrub_prose(it["note"])
for a in data["advisories"]:
    if a.get("text"): a["text"] = scrub_prose(a["text"])

# reconcile.py turns these observations into closure history and writes flood_data.json from it.
json.dump(data, open(os.path.join(HERE, "observations.json"), "w"), indent=1)
print(f"items={len(data['items'])} (manual {len(manual)}) alerts={len(alerts)} errors={errors}")
