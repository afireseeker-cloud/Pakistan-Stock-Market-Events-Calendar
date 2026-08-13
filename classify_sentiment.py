"""
Adds sentiment (positive/negative/neutral/mixed) to events in a merged
calendar file. Hybrid approach:

  1. RULE-BASED, no API call, for types where direction is structurally
     unambiguous:
       - delisting            -> always negative
       - market_holiday, mpc*, spi/cpi/wpi/pama, book_closure, ipo_* ->
         skipped entirely (procedural/mechanical, not "good or bad news" --
         forcing a sentiment onto "PSX closed for Independence Day" would
         be manufacturing a signal that doesn't exist)

  2. LLM-BASED, via Google Gemini's free API tier, for genuinely ambiguous
     free text:
       - results, other_announcement, other_psx_notice, governance_change,
         director_disclosure
     Batched (default 15 per call) into one prompt asking for a strict
     JSON array back, using Gemini's responseSchema to constrain the
     output shape server-side rather than just asking nicely and hoping.

     Same free-tier model as extract_meeting_dates.py (gemini-3.1-flash-
     lite, confirmed working) -- no separate signup needed if you've
     already set that up. Uses GEMINI_API_KEY, same variable both scripts
     read, no new setup at all if you're running both.

CACHING: every classification is written to sentiment_cache.json, keyed by
event id. Re-running this script on a merged file that includes
already-seen events skips them entirely -- only genuinely new events cost
an API call.

Setup (same as extract_meeting_dates.py):
    Get a free key at https://aistudio.google.com (no credit card)
    set GEMINI_API_KEY=your_key_here          (Windows)
    export GEMINI_API_KEY=your_key_here        (Mac/Linux)

Run:
    python3 classify_sentiment.py events_merged.json --out events_merged.json
"""
import argparse
import json
import os
import sys
import time

import requests

GEMINI_MODEL = "gemini-3.1-flash-lite"  # same model confirmed working in extract_meeting_dates.py;
                                          # if it stops working, check https://aistudio.google.com
CACHE_FILE = "sentiment_cache.json"
BATCH_SIZE = 15
RATE_LIMIT_SECONDS = 4.5  # stays comfortably under Gemini free tier's ~15 RPM
MAX_RETRIES = 3

ALLOWED_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}

# types with an unambiguous, rule-derivable sentiment -- never sent to the LLM
RULE_BASED = {
    "delisting": "negative",
}

# types where "sentiment" isn't a meaningful concept -- skipped entirely,
# never given a fabricated neutral/positive just to fill the field
NO_SENTIMENT_TYPES = {
    "mpc", "mpc_briefing", "mpc_presser", "mpc_minutes", "mpc_report",
    "spi_weekly", "cpi_monthly", "wpi_monthly", "auto_sales_monthly",
    "market_holiday", "book_closure", "dividend_announced",
    "ipo_subscription", "ipo_book_building", "ipo_prospectus", "ipo_listing",
    "fund_listing", "debt_instrument_listing", "board_meeting", "agm",
    "corporate_briefing", "trading_halt", "risk_warning",
}

# types worth an actual LLM judgment call
LLM_CANDIDATE_TYPES = {
    "results", "other_announcement", "other_psx_notice",
    "governance_change", "director_disclosure",
}

SYSTEM_PROMPT = """You classify PSX (Pakistan Stock Exchange) company announcement titles by investor sentiment.

For each item, assign exactly one of: positive, negative, neutral, mixed.
- positive: clearly good news for shareholders (e.g. strong results, favorable court ruling, new contract win)
- negative: clearly bad news (e.g. weak results, regulatory penalty, executive departure under a cloud)
- neutral: procedural or informational, no clear direction (e.g. routine appointment, standard disclosure)
- mixed: genuinely has both positive and negative elements (e.g. revenue up but margins down)

Base your judgment only on the title text given. If a title is too generic to judge (e.g. "Material Information" with no other context), classify it as neutral rather than guessing."""

GEMINI_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "sentiment": {"type": "STRING", "enum": list(ALLOWED_SENTIMENTS)},
        },
        "required": ["id", "sentiment"],
    },
}


def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def classify_batch(events: list[dict], api_key: str, model: str) -> dict:
    """Sends one batch to Gemini, returns {id: sentiment}."""
    items = [{"id": e["id"], "title": e["title"]} for e in events]
    user_message = f"{SYSTEM_PROMPT}\n\nClassify these:\n{json.dumps(items, indent=2)}"

    body = {
        "contents": [{"parts": [{"text": user_message}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_RESPONSE_SCHEMA,
        },
    }
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(f"{api_url}?key={api_key}", json=body, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)

            results = {}
            for item in parsed:
                sentiment = item.get("sentiment", "").lower()
                if sentiment not in ALLOWED_SENTIMENTS:
                    print(f"WARNING: unexpected sentiment value {sentiment!r} "
                          f"for id {item.get('id')}, skipping", file=sys.stderr)
                    continue
                results[item["id"]] = sentiment
            return results

        except requests.RequestException as e:
            last_error = e
            wait = 2 ** attempt
            print(f"WARNING: Gemini call failed (attempt {attempt + 1}/{MAX_RETRIES}): "
                  f"{e}. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"WARNING: could not parse Gemini response as expected JSON: {e}",
                  file=sys.stderr)
            return {}

    print(f"WARNING: giving up on this batch after {MAX_RETRIES} attempts: "
          f"{last_error}", file=sys.stderr)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="merged events JSON file")
    ap.add_argument("--out", default=None,
                     help="output path, defaults to overwriting the input file")
    ap.add_argument("--model", default=GEMINI_MODEL,
                     help=f"Gemini model to use, default {GEMINI_MODEL}")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set. Get a free key (no credit card) at "
              "https://aistudio.google.com -- same variable extract_meeting_dates.py "
              "uses, no new setup if you've already got that working.", file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        events = json.load(f)

    cache = load_cache()

    rule_classified = 0
    already_cached = 0
    skipped_no_sentiment = 0
    to_classify = []

    for e in events:
        if e["id"] in cache:
            e["sentiment"] = cache[e["id"]]
            already_cached += 1
        elif e["type"] in RULE_BASED:
            sentiment = RULE_BASED[e["type"]]
            e["sentiment"] = sentiment
            cache[e["id"]] = sentiment
            rule_classified += 1
        elif e["type"] in NO_SENTIMENT_TYPES:
            skipped_no_sentiment += 1
        elif e["type"] in LLM_CANDIDATE_TYPES:
            to_classify.append(e)
        else:
            # unrecognized type -- don't guess, don't crash, just skip
            skipped_no_sentiment += 1

    print(f"INFO: {already_cached} already cached, {rule_classified} rule-classified, "
          f"{skipped_no_sentiment} skipped (no sentiment applicable), "
          f"{len(to_classify)} need a Gemini call", file=sys.stderr)

    total_batches = (len(to_classify) + args.batch_size - 1) // args.batch_size or 1
    for i in range(0, len(to_classify), args.batch_size):
        batch = to_classify[i:i + args.batch_size]
        batch_num = i // args.batch_size + 1
        print(f"INFO: classifying batch {batch_num}/{total_batches} "
              f"({len(batch)} events)...", file=sys.stderr)
        results = classify_batch(batch, api_key, args.model)
        for e in batch:
            if e["id"] in results:
                e["sentiment"] = results[e["id"]]
                cache[e["id"]] = results[e["id"]]
        if i + args.batch_size < len(to_classify):
            time.sleep(RATE_LIMIT_SECONDS)

    save_cache(cache)

    out_path = args.out or args.input
    with open(out_path, "w") as f:
        json.dump(events, f, indent=2)

    classified_total = sum(1 for e in events if e.get("sentiment"))
    print(f"\nDONE: {classified_total}/{len(events)} events now have sentiment, "
          f"written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
