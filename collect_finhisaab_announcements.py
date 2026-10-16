"""
Collects FinHisaab announcement results from the current Claude session and
merges them into data/finhisaab_announcements.json.

FinHisaab has no public API, and PSX's own site returns 403 to GitHub
Actions, so company events are pulled by a scheduled Claude task: it calls
FinHisaab's research_announcements tool (upcoming_events and feed modes)
through the connector, then runs this script. Rather than have the agent
retype hundreds of records, this script reads the tool results straight out
of the session transcript (~/.claude/projects/*/*.jsonl), including results
that were too large to show inline and were saved to a tool-results file.

Each item is slimmed to the fields the calendar uses, keyed by announcement
id, and merged with what's already in the file. Items whose announcement and
event dates are both older than KEEP_DAYS are dropped.

Run (inside the Claude session, after the FinHisaab calls):
    python collect_finhisaab_announcements.py
    python collect_finhisaab_announcements.py --transcript path/to/session.jsonl
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

OUT_FILE = "data/finhisaab_announcements.json"
KEEP_DAYS = 120
TOOL_NAME_RE = re.compile(r"Finhisaab__research_announcements$", re.I)
PERSISTED_RE = re.compile(r"saved to:\s*(\S+\.(?:txt|json))")

KEEP_FIELDS = (
    "id", "symbol", "companyName", "sector", "isShariahCompliant",
    "announcementDate", "category", "displayCategory", "title",
    "documentUrl", "imageUrl", "summary", "sentiment", "impact", "newsType",
    "briefingSubtype", "eventType", "eventStatus", "eventDate", "eventDateTime",
)
BOARD_FIELDS = ("meetingStatus", "meetingDate", "meetingTime", "purpose",
                "willConsiderFinancialResults", "willConsiderEntitlement",
                "financialPeriodEnded", "financialPeriodType")
BRIEFING_FIELDS = ("eventStatus", "briefingDate", "briefingTime", "isVirtual",
                   "financialPeriodEnded", "financialPeriodType")


def newest_transcripts() -> list[str]:
    paths = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    paths.sort(key=os.path.getmtime, reverse=True)
    return paths[:1]


def result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if x.get("type") == "text")
    return c or ""


def load_result_json(text: str) -> dict | None:
    text = text.strip()
    m = PERSISTED_RE.search(text)
    if text.startswith("<persisted-output>") and m:
        try:
            with open(m.group(1)) as f:
                text = f.read()
        except OSError:
            return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def items_from_transcript(path: str) -> list[dict]:
    uses, items, calls = set(), [], 0
    with open(path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if b.get("type") == "tool_use" and TOOL_NAME_RE.search(b.get("name", "")):
                    uses.add(b["id"])
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses:
                    data = load_result_json(result_text(b))
                    if data and isinstance(data.get("data", {}).get("items"), list):
                        calls += 1
                        items.extend(data["data"]["items"])
    print(f"INFO: {calls} FinHisaab announcement results, {len(items)} items in {path}",
          file=sys.stderr)
    return items


def slim(item: dict) -> dict:
    out = {k: item.get(k) for k in KEEP_FIELDS}
    bm = item.get("boardMeeting")
    if bm:
        out["boardMeeting"] = {k: bm.get(k) for k in BOARD_FIELDS}
    cb = item.get("corporateBriefing")
    if cb:
        out["corporateBriefing"] = {k: cb.get(k) for k in BRIEFING_FIELDS}
    fr = item.get("financialResults")
    if fr:
        out["financialResults"] = {
            k: fr.get(k) for k in ("reportType", "periodEndDate", "figuresUnit",
                                   "periodFigures", "quarterFigures", "dividend",
                                   "boardDecisions")
            if fr.get(k) is not None
        }
    if item.get("dividendExtraction"):
        out["dividendExtraction"] = item["dividendExtraction"]
    return out


def still_relevant(item: dict, cutoff: str) -> bool:
    dates = [d[:10] for d in (item.get("announcementDate"), item.get("eventDate")) if d]
    return any(d >= cutoff for d in dates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", action="append",
                    help="session transcript(s) to read; default: newest one")
    ap.add_argument("--out", default=OUT_FILE)
    args = ap.parse_args()

    transcripts = args.transcript or newest_transcripts()
    if not transcripts:
        sys.exit("ERROR: no session transcript found")

    new_items = []
    for t in transcripts:
        new_items.extend(items_from_transcript(t))
    if not new_items:
        sys.exit("ERROR: no FinHisaab announcement items found -- nothing written")

    try:
        with open(args.out) as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = {"items": {}}

    by_id = existing.get("items", {})
    added = len({i["id"] for i in new_items if i.get("id")} - set(by_id))
    for i in new_items:
        if i.get("id"):
            by_id[i["id"]] = slim(i)

    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    by_id = {k: v for k, v in by_id.items() if still_relevant(v, cutoff)}

    out = {
        "_about": "FinHisaab PSX announcements, collected by a scheduled Claude task. "
                  "ingest_finhisaab_announcements.py turns these into calendar events. "
                  "Do not edit by hand.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": dict(sorted(by_id.items(),
                             key=lambda kv: kv[1].get("announcementDate") or "")),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)

    print(f"DONE: {added} new, {len(by_id)} total announcements in {args.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
