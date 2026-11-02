"""Find where a closure starts and ends: walk the closed road from the reported spot to the
nearest cross street on each side, using Brevard County's public 911 street centerline layer.
Agencies (FL511, FHP, police) report a single spot; this turns it into 'from A to B' plus the
stretch of road between them. Results are cached per report and location."""
import json, math, os, re, urllib.parse, urllib.request

LAYER = "https://gis.brevardfl.gov/gissrv/rest/services/Emergency_Management/9_1_1_Emergency_Response_Layers/MapServer/1/query"
UA = "BrevardFloodMap/0.1 (+https://brevardemergency.com)"
MAX_WALK_M = 2500
KEEP_UPPER = {"N","S","E","W","NE","NW","SE","SW","US","SR","CR","I","A1A","II","III"}

def nice(name):
    words = [w if (w in KEEP_UPPER or re.search(r"\d", w)) else w.capitalize() for w in (name or "").split()]
    return " ".join({"Av": "Ave", "Pkwy": "Pkwy"}.get(w, w) for w in words)

def norm(s):
    s = (s or "").upper().replace("-", " ").replace(".", "")
    s = re.sub(r"\bSTATE ROAD\b", "SR", s); s = re.sub(r"\bCOUNTY ROAD\b", "CR", s)
    s = re.sub(r"\bUS HWY\b|\bUS HIGHWAY\b", "US", s); s = re.sub(r"\bINTERSTATE\b", "I", s)
    s = re.sub(r"\bAVENUE\b|\bAVE\b", "AV", s); s = re.sub(r"\bBOULEVARD\b", "BLVD", s); s = re.sub(r"\bROAD\b", "RD", s); s = re.sub(r"\bSTREET\b", "ST", s)
    return re.sub(r"\s+", " ", s).strip()

def aliases(item):
    out = set()
    for raw in [item.get("roadShort"), item.get("road")] + (item.get("road") or "").split(" / "):
        n = norm(raw)
        if not n: continue
        n = re.sub(r"\b(NORTHBOUND|SOUTHBOUND|EASTBOUND|WESTBOUND|NB|SB|EB|WB)\b.*$", "", n).strip()
        n = re.split(r"\b(AT|BETWEEN|NEAR|BEYOND)\b", n)[0].strip()
        if len(n) >= 3: out.add(n)
    return out

def m_xy(lat0):
    k = math.cos(math.radians(lat0))
    return lambda lat, lng: ((lng) * 111320 * k, lat * 110540)

def fetch_streets(lat, lng, radius=1500):
    q = urllib.parse.urlencode({"geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": radius, "units": "esriSRUnit_Meter",
        "outFields": "ADD_LBL,SYMB,OneWay", "outSR": 4326, "returnGeometry": "true", "f": "json"})
    req = urllib.request.Request(LAYER + "?" + q, headers={"User-Agent": UA})
    d = json.load(urllib.request.urlopen(req, timeout=40))
    if d.get("error"): raise RuntimeError(d["error"])
    segs = []
    for f in d.get("features", []):
        a = f["attributes"]
        for path in f["geometry"]["paths"]:
            pts = [(round(y, 6), round(x, 6)) for x, y in path]
            if len(pts) >= 2: segs.append({"name": (a.get("ADD_LBL") or "").strip(), "symb": (a.get("SYMB") or "").strip(), "pts": pts})
    return segs

def is_road(seg, al):
    n, s = norm(seg["name"]), norm(seg["symb"])
    if s and s in al: return True
    for a in al:  # county layer stores state/US/interstate routes as the bare number ("192", "507", "1", "95")
        m = re.fullmatch(r"(US|SR|I) (\w+)", a)
        if m and s == m.group(2): return True
    for a in al:
        if n == a or (len(a) >= 6 and a in n): return True
    return False

def extent(item, segs):
    lat, lng = item["lat"], item["lng"]
    al = aliases(item)
    if not al: return None
    xy = m_xy(lat); P = xy(lat, lng)
    road = [s for s in segs if is_road(s, al)]
    if not road: return None
    # a road keeps its route number when its name changes (Minton Rd -> S Wickham Rd are both CR 509)
    rsym = {s["symb"] for s in road if len(s["symb"]) > 1}
    onroad = lambda seg: is_road(seg, al) or (seg["symb"] in rsym)
    road = [s for s in segs if onroad(s)]
    def proj(a, b):
        ax, ay = xy(*a); bx, by = xy(*b); dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy or 1e-9
        t = max(0, min(1, ((P[0] - ax) * dx + (P[1] - ay) * dy) / L2))
        return t, math.hypot(ax + t * dx - P[0], ay + t * dy - P[1])
    best = None
    for s in road:
        for i in range(len(s["pts"]) - 1):
            t, d = proj(s["pts"][i], s["pts"][i + 1])
            if best is None or d < best[0]: best = (d, s, i, t)
    if best[0] > 150: return None
    d0, s0, i0, t0 = best
    a, b = s0["pts"][i0], s0["pts"][i0 + 1]
    snap = (round(a[0] + (b[0] - a[0]) * t0, 6), round(a[1] + (b[1] - a[1]) * t0, 6))
    # endpoint index: node -> segments touching it
    ends = {}
    for s in segs:
        for e in (s["pts"][0], s["pts"][-1]): ends.setdefault(e, []).append(s)
    road_names = {norm(s["name"]) for s in road} | al
    spot = norm(re.sub(r"^(at|beyond|near)\s+", "", item.get("spot") or "", flags=re.I))
    spot_parts = {p.strip() for p in re.split(r"/|&| AND ", spot) if p.strip()}
    def cross_at(node, walked):
        for s in ends.get(node, []):
            if onroad(s): continue
            n = norm(s["name"])
            if not n or n in road_names or n in ("TURNAROUND", "RAMP", "PRIVATE"): continue
            if walked < 200 and any(p and (p in n or n in p) for p in spot_parts): continue  # the reported spot itself
            return nice(s["name"])
        return None
    def walk(first_pts, seg):
        """first_pts: path from snap point toward a node; then continue along road segments."""
        path, walked, used = list(first_pts), 0.0, {id(seg)}
        for k in range(1, len(path)): walked += math.dist(xy(*path[k - 1]), xy(*path[k]))
        while walked < MAX_WALK_M:
            node = path[-1]
            c = cross_at(node, walked)
            if c: return c, path, walked
            nxt = [s for s in ends.get(node, []) if onroad(s) and id(s) not in used]
            if not nxt: return None, path, walked
            # keep going as straight as possible
            px, py = xy(*path[-2]); nx, ny = xy(*node); hx, hy = nx - px, ny - py
            def turn(s):
                pts = s["pts"] if s["pts"][0] == node else s["pts"][::-1]
                qx, qy = xy(*pts[1]); vx, vy = qx - nx, qy - ny
                return -((hx * vx + hy * vy) / ((math.hypot(hx, hy) * math.hypot(vx, vy)) or 1))
            s = min(nxt, key=turn); used.add(id(s))
            pts = s["pts"] if s["pts"][0] == node else s["pts"][::-1]
            for k in range(1, len(pts)):
                walked += math.dist(xy(*pts[k - 1]), xy(*pts[k])); path.append(pts[k])
        return None, path, walked
    fwd = [snap] + s0["pts"][i0 + 1:]
    back = [snap] + s0["pts"][:i0 + 1][::-1]
    c1, p1, w1 = walk(fwd, s0)
    c2, p2, w2 = walk(back, s0)
    if not (c1 and c2): return None
    line = p2[::-1] + p1[1:]
    A, B = (c2, c1)
    # order from/to by travel direction (or west->east / south->north)
    first, last = line[0], line[-1]
    dirn = (item.get("dir") or "").upper()
    flip = {"EB": first[1] > last[1], "WB": first[1] < last[1], "NB": first[0] > last[0], "SB": first[0] < last[0]}.get(dirn)
    if flip is None:
        flip = (first[1] > last[1]) if abs(first[1] - last[1]) * math.cos(math.radians(lat)) > abs(first[0] - last[0]) else (first[0] > last[0])
    if flip: line, A, B = line[::-1], B, A
    return {"from": A, "to": B, "line": [[p[0], p[1]] for p in line], "length_m": round(w1 + w2)}

def add_extents(items, here, is_closed):
    path = os.path.join(here, "closure_extent_cache.json")
    try: cache = json.load(open(path))
    except Exception: cache = {}
    live, errs = {}, []
    for it in items:
        if it.get("lat") is None or not is_closed(it): continue
        key = f"{it['id']}|{round(it['lat'],5)},{round(it['lng'],5)}|{it.get('roadShort') or it.get('road')}"
        if key not in cache:
            try: cache[key] = extent(it, fetch_streets(it["lat"], it["lng"])) or {}
            except Exception as e: errs.append(str(e)[:80]); continue
        live[key] = cache[key]
        if cache[key]:
            it["from"], it["to"], it["line"] = cache[key]["from"], cache[key]["to"], cache[key]["line"]
    json.dump(live, open(path, "w"))
    return errs
