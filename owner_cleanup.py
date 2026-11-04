"""Mike's one-time Nov4 cleanup, not an agency reopening or a standing feed-only policy.
Runs once per Florida county after both feeds are healthy. The immutable history remains; no open/green claim.
"""
import json, math, os, re
import events as E
import reconcile as R

CUTOFF = "2026-11-04T18:38:15Z"
RULE = "One-time owner cleanup: remove older flood/closure reports absent from healthy FHP/FL511 listings."
COUNTIES = {"brevard", "flagler", "volusia", "orange", "indian-river"}
NEAR_M = 400  # nearby/uncertain evidence keeps the warning, never proves a reopening


def eligible(r):
    f = r["fields"]
    return (r["status"] == "active" and r["src"] not in E.FEED_DROP
            and E.parse(r["firstReported"]) < E.parse(CUTOFF) and E.parse(r["lastReported"]) < E.parse(CUTOFF)
            and (f.get("status") in ("closed", "flooded") or f.get("fullClosure")
                 or (f.get("status") in ("caution", "hazard") and re.search(r"\bflood(?:ed|ing)?\b", " ".join(str(f.get(k) or "") for k in ("title", "note", "lanes")), re.I))))


def related(a, b):
    if R.same_spot(a, b): return True
    # If either location is unplaced, a shared named road is uncertain: preserve it rather than call it absent.
    def names(f):
        k=R.spot_key(f)
        return set(k[0]) if k else {E.road_words(f.get("road") or "")}-{ "" }
    def point(f):
        try:
            p=(float(f["lat"]),float(f["lng"]))
            return p if all(math.isfinite(v) for v in p) else None
        except (TypeError,ValueError,KeyError): return None
    pa,pb=point(a),point(b)
    if not pa or not pb: return bool(names(a)&names(b))
    dist=6371000*math.hypot(math.radians(pb[0]-pa[0]),math.radians(pb[1]-pa[1])*math.cos(math.radians(pa[0])))
    return dist <= NEAR_M


def current_feeds(state):
    return [r for r in state["records"].values() if r["status"]=="active" and r["src"] in E.FEED_DROP and not r["missingSince"]]


def safe_to_remove(r,state):
    return (eligible(r) and all(state["sources"].get(s,{}).get("state")=="up" for s in E.FEED_DROP)
            and not any(related(r["fields"],f["fields"]) for f in current_feeds(state)))


def execute(obs,state,now,county,path=None,marker=None):
    """One successful batch; retained counterparts are never removed by this cleanup on a later day."""
    path=path or E.EVENTS
    marker=marker or os.path.join(E.HERE,"audit/owner_cleanup.json")
    if os.path.exists(marker):
        receipt=json.load(open(marker))
        if receipt.get("cutoff")!=CUTOFF or not receipt.get("completedAt"):
            raise ValueError("unrecognised owner cleanup receipt; refusing to rerun")
        return [], receipt
    checked=E.parse(obs.get("generated"))
    if (county not in COUNTIES or not checked or not 0 <= (now-checked).total_seconds() <= 300
            or not all(obs.get("sources",{}).get(s)=="up" for s in E.FEED_DROP)):
        return [], None
    # Recovery after a crash between append and marker: do not start another batch against a different live snapshot.
    prior=[ev for ev in E.load(path) if ev["kind"]=="cleared" and (ev.get("evidence") or {}).get("kind")=="owner-cleanup"]
    recovered=bool(prior)
    before=len(R.display(state)[0])
    planned=[] if recovered else [{"kind":"cleared","rid":r["rid"],"src":r["src"],"actor":"owner-cleanup","stime":E.iso(now),
                 "evidence":{"kind":"owner-cleanup","cutoff":CUTOFF,"rule":RULE,"checkedAt":obs["generated"],"reopenedAt":""}}
                for r in state["records"].values() if safe_to_remove(r,state)]
    written=E.append(planned,path,clearance_enabled=True,now=now)
    after=E.derive(E.load(path),now)
    removed=sorted({ev["rid"] for ev in prior+written})
    receipt={"cutoff":CUTOFF,"completedAt":E.iso(now),"rule":RULE,"county":county,"before":None if recovered else before,
             "after":len(R.display(after)[0]),"removed":removed,"recoveredAfterInterruptedBatch":recovered,
             "note":"Administrative removal; does not confirm any road reopened."}
    os.makedirs(os.path.dirname(marker),exist_ok=True)
    with open(marker+".tmp","w") as f:json.dump(receipt,f,indent=1)
    os.replace(marker+".tmp",marker)
    return written,receipt
