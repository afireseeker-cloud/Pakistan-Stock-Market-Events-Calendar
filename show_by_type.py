"""
Prints all events of a given type from a JSONL events file.

Usage:
    python show_by_type.py events_psx_notices.jsonl ipo_listing
"""
import json
import sys

if len(sys.argv) != 3:
    print("Usage: python show_by_type.py <file.jsonl> <type>")
    sys.exit(1)

path, event_type = sys.argv[1], sys.argv[2]

count = 0
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e["type"] == event_type:
            count += 1
            print(f"{e['date']}  |  {e['title']}")

print(f"\n{count} events of type '{event_type}'", file=sys.stderr)
