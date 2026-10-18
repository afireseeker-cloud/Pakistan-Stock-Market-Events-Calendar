import subprocess, json, collections, sys
def sh(c):
    print("$",c, flush=True); r=subprocess.run(c,shell=True,capture_output=True,text=True); print(r.stdout[-3000:]); print(r.stderr[-6000:]); return r
sh("git fetch -q --depth 1 origin gh-pages && git show origin/gh-pages:events_merged.json > live.json")
sh("python fetch_macro_values.py live.json --out macro_values.json")
print(open("macro_values.json").read()[:4000])
sh("python fetch_psx_announcements.py --from 2026-10-01 --to 2026-10-18 --out a.jsonl")
ev=[json.loads(l) for l in open("a.jsonl")] if __import__('os').path.exists("a.jsonl") else []
print(len(ev), collections.Counter(e["type"] for e in ev).most_common())
print([ (e["symbol"],e["date"],e["title"][:60]) for e in ev if e["type"]=="board_meeting"][:15])
sh("python fetch_psx_payouts.py --out p.jsonl")
pv=[json.loads(l) for l in open("p.jsonl")] if __import__('os').path.exists("p.jsonl") else []
print(len(pv), collections.Counter(e["type"] for e in pv).most_common()); print([ (e["symbol"],e["date"],e["title"][:60]) for e in pv if e["date"]>="2026-10-18"][:15])
# rerun
