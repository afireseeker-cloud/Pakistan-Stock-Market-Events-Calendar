"""
Sanity check for a real events_psx.jsonl file. Run this in the same folder
as your output:

    python3 inspect_events.py events_psx.jsonl

Prints:
  - type distribution (how much is board_meeting/results/etc vs
    other_announcement noise)
  - cancelled/revoked count
  - a handful of unclassified titles, to see if the regex rules need
    widening for patterns not in the original sample
  - any rows with missing/null symbol or date, which would indicate a
    parsing bug rather than expected data
"""
import json
import sys
from collections import Counter


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "events_psx.jsonl"

    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"Total events: {len(events)}\n")

    type_counts = Counter(e["type"] for e in events)
    print("Type distribution:")
    for t, count in type_counts.most_common():
        pct = 100 * count / len(events)
        print(f"  {t:22s} {count:5d}  ({pct:5.1f}%)")

    cancelled = [e for e in events if e["status"] == "cancelled"]
    print(f"\nCancelled/revoked: {len(cancelled)}")
    for e in cancelled[:5]:
        print(f"  {e['date']}  {e['symbol']:10s} {e['title'][:60]}")

    unclassified = [e for e in events if e["type"] == "other_announcement"]
    print(f"\nUnclassified sample ({len(unclassified)} total, showing 10):")
    for e in unclassified[:10]:
        print(f"  {e['title'][:70]}")

    broken = [e for e in events if not e.get("symbol") or not e.get("date")]
    print(f"\nRows with missing symbol or date: {len(broken)}")
    for e in broken[:5]:
        print(f"  {e}")

    no_attachment = [e for e in events
                      if not e["payload"].get("pdf_url")
                      and not e["payload"].get("image_url")]
    print(f"\nRows with no attachment link at all: {len(no_attachment)}")


if __name__ == "__main__":
    main()
