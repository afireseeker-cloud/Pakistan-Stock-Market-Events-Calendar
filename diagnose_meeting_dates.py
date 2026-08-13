"""
Shows which events meeting-date extraction couldn't resolve, grouped by
type, with sample titles -- lets you tell "expected, no date to extract"
(retrospective minutes, post-meeting slide decks) apart from "should have
matched but didn't" (a real notice with unusual phrasing) without guessing.

Run: python3 diagnose_meeting_dates.py events_merged.json
"""
import json
import sys
from collections import Counter

events_path = sys.argv[1] if len(sys.argv) > 1 else "events_merged.json"

with open("meeting_dates_cache.json") as f:
    cache = json.load(f)
with open(events_path) as f:
    events = json.load(f)

events_by_id = {e["id"]: e for e in events}

unresolved = []
for event_id, entry in cache.items():
    if entry.get("scheduled_date") is None:
        e = events_by_id.get(event_id)
        if e:
            unresolved.append(e)

print(f"Total unresolved: {len(unresolved)}\n")

type_counts = Counter(e["type"] for e in unresolved)
print("By type:")
for t, count in type_counts.most_common():
    print(f"  {t:20s} {count}")

# retrospective-sounding titles suggest "no future date to extract" is the
# correct, expected outcome rather than a real gap
RETROSPECTIVE_MARKERS = ["minutes of", "certified copy", "resolution", "presentation",
                          "video recording", "in progress", "concluded"]
retrospective = [e for e in unresolved
                  if any(m in e.get("title", "").lower() for m in RETROSPECTIVE_MARKERS)]
print(f"\nLikely retrospective (no future date to find, expected miss): {len(retrospective)}")

genuinely_unclear = [e for e in unresolved if e not in retrospective]
print(f"Everything else (worth a closer look): {len(genuinely_unclear)}\n")

print("Sample titles from 'everything else' (first 20):")
for e in genuinely_unclear[:20]:
    pdf = e.get("payload", {}).get("pdf_url", "no pdf")
    print(f"  [{e['type']}] {e.get('title', '')[:70]}")
    print(f"      {pdf}")
