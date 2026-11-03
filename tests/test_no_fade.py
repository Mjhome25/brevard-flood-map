"""Phase 0 checks: old manual closures are never deleted (success or error path), and sweep reopen output is held as a
proposal without touching the closure. Runs news_sweep.main against a temp copy with a fake Claude."""
import json, os, shutil, sys, tempfile
repo = sys.argv[1]
tmp = tempfile.mkdtemp()
for f in ("news_sweep.py", "manual_closures.json", "advisories.json"): shutil.copy(os.path.join(repo, f), tmp)
sys.path.insert(0, tmp); os.environ["ANTHROPIC_API_KEY"] = "test-not-a-key"
import news_sweep as ns
before = json.load(open(os.path.join(tmp, "manual_closures.json")))
ids = [c["id"] for c in before]
fails = 0
def check(label, ok):
    global fails; print(("ok   " if ok else "FAIL ") + label); fails += not ok
# 1. error path: Claude fails, clock 1000 days ahead
import datetime
real_now = ns.now
ns.now = lambda: real_now() + datetime.timedelta(days=1000)
def boom(p): raise RuntimeError("down")
ns.ask_claude = boom
sys.argv = ["x", "--force"]; ns.main()
after = json.load(open(os.path.join(tmp, "manual_closures.json")))
check("error path, 1000 days later: all %d closures kept" % len(ids), [c["id"] for c in after] == ids)
# 2. success path with reopen output for full and partial
ns.ask_claude = lambda p: {"closures": [], "advisories": [], "reopened": [
    {"id": ids[0], "partial": False, "source": "X", "sourceUrl": "https://x.example", "quote": "q", "time": "2026-11-03T09:00:00Z"},
    {"id": ids[1], "partial": True, "source": "Y", "time": "2026-11-03T09:00:00Z"}]}
ns.main()
after = json.load(open(os.path.join(tmp, "manual_closures.json")))
check("success path, 1000 days later: no closure deleted", [c["id"] for c in after] == ids)
check("reopen output changed no closure (status, note, updated)", after == before)
props = json.load(open(os.path.join(tmp, "reopen_proposals.json")))
check("both reopen reports held as proposals", sorted(p["id"] for p in props) == sorted(ids[:2]))
ns.main()
check("repeat run does not duplicate proposals", len(json.load(open(os.path.join(tmp, "reopen_proposals.json")))) == 2)
print("failures:", fails); sys.exit(fails)
