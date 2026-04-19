#!/usr/bin/env python3
"""
patch_placeholders.py
=====================
Targeted patch that finds all items in items_enriched.json with
"See official source" placeholder stats, generates real stats + flavor
using Claude (from the entries data already in the file), and writes
a fixed output file.

Usage:
    python patch_placeholders.py \
        --input items_enriched.json \
        --output items_enriched_patched.json \
        --api-key sk-ant-...
        [--limit 50]      # process only N items (for testing)
        [--dry-run]       # print what would be done, don't call API or write
        [--skip-ai]       # use first 150 chars of entries as stats (no API)
"""

import argparse
import json
import re
import sys
import time

# ─────────────────────────────────────────────────────────────
# UTILITIES (copied from build_items_db.py)
# ─────────────────────────────────────────────────────────────

TAG_RE        = re.compile(r'\{@\w+\s([^}|]+)(?:\|[^}]*)?\}')
SIMPLE_TAG_RE = re.compile(r'\{@\w+\}')

def clean_tags(text):
    if not isinstance(text, str):
        return str(text) if text else ""
    text = TAG_RE.sub(r'\1', text)
    text = SIMPLE_TAG_RE.sub('', text)
    return text.strip()

def entries_to_text(entries, max_chars=1500):
    """Flatten 5etools entries array to plain text."""
    parts = []
    for entry in entries:
        if isinstance(entry, str):
            parts.append(clean_tags(entry))
        elif isinstance(entry, dict):
            t = entry.get("type", "")
            if t in ("entries", "inset", "section"):
                name = entry.get("name", "")
                if name:
                    parts.append(f"{name}:")
                parts.extend(_flatten(entry.get("entries", [])))
            elif t == "list":
                for item in entry.get("items", []):
                    if isinstance(item, str):
                        parts.append("• " + clean_tags(item))
                    elif isinstance(item, dict):
                        n = item.get("name", "")
                        e = " ".join(
                            clean_tags(x) for x in item.get("entries", [])
                            if isinstance(x, str)
                        )
                        parts.append(f"• {n}: {e}" if n else f"• {e}")
            elif t == "table":
                parts.append("[Table omitted]")
    text = " ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text

def _flatten(entries):
    out = []
    for e in entries:
        if isinstance(e, str):
            out.append(clean_tags(e))
        elif isinstance(e, dict) and "entries" in e:
            out.extend(_flatten(e["entries"]))
    return out

# ─────────────────────────────────────────────────────────────
# AI GENERATION
# ─────────────────────────────────────────────────────────────

def generate_stats_and_flavor(client, item_name, entries_text):
    """
    Call Claude to generate a stats shorthand + flavor sentence.
    Returns (stats_str, flavor_str).
    Retries up to 3 times on parse failure.
    """
    prompt = f"""Item: {item_name}
Full description: {entries_text[:1000]}

Output ONLY a JSON object with exactly two fields:
{{
  "stats": "mechanics summary in 25 words or less — bonuses, charges, spells, conditions, key numbers",
  "flavor": "one evocative slightly wry sentence (15 words max) like a merchant's private note"
}}

For stats: be mechanical and specific. Include numbers, charges, spell names, bonus amounts.
Examples of good stats:
- "+2 longsword. On a hit: extra 2d6 fire damage. Sheds bright light 40 ft on command."
- "10 charges. Cure Wounds, Lesser Restoration, Mass Cure Wounds. Regains 1d6+4 at dawn."
- "Holds 500 lbs in 64 cu ft. Weighs 15 lbs. Turning inside out destroys contents."
- "Advantage on Stealth checks. No movement speed penalty in heavy armor."

Output ONLY the JSON object, no other text."""

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",  # Fast + cheap for batch work
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            text = resp.content[0].text.strip()
            # Strip markdown fences if present
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            data = json.loads(text)
            stats  = data.get("stats", "").strip()
            flavor = data.get("flavor", "").strip().strip('"')
            if stats:
                return stats, flavor
        except (json.JSONDecodeError, KeyError):
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            print(f"      API error (attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2)

    # Fallback: use raw entries text truncated
    return entries_text[:150].rstrip() + "…", "A remarkable item."

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def is_placeholder(item):
    stats = (item.get("stats") or "").strip().lower()
    return (
        "see official source" in stats
        or stats == ""
        or stats == "see official source for full description."
    )

def main():
    parser = argparse.ArgumentParser(description="Patch placeholder stats in items_enriched.json")
    parser.add_argument("--input",   required=True,  help="Path to items_enriched.json")
    parser.add_argument("--output",  required=True,  help="Path for patched output file")
    parser.add_argument("--api-key", required=False, help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    parser.add_argument("--limit",   type=int,       help="Max items to process (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Report stats, don't write output")
    parser.add_argument("--skip-ai", action="store_true", help="Use raw entries text, skip API calls")
    args = parser.parse_args()

    # ── Load ─────────────────────────────────────────────────
    print(f"Loading {args.input}...")
    with open(args.input, encoding="utf-8") as f:
        items = json.load(f)
    print(f"  Total items: {len(items)}")

    # ── Find placeholders ─────────────────────────────────────
    placeholders = [i for i in items if is_placeholder(i)]
    has_entries  = [i for i in placeholders if i.get("entries")]
    no_entries   = [i for i in placeholders if not i.get("entries")]

    print(f"  Placeholder stats:   {len(placeholders)}")
    print(f"    With entries data: {len(has_entries)}  ← will be patched")
    print(f"    No entries data:   {len(no_entries)}  ← will be left as-is")

    to_patch = has_entries
    if args.limit:
        to_patch = to_patch[:args.limit]
        print(f"  (Limited to {args.limit} items for this run)")

    if args.dry_run:
        print("\n[DRY RUN — showing first 10 that would be patched]")
        for item in to_patch[:10]:
            entries_text = entries_to_text(item["entries"])
            print(f"  {item['name']}")
            print(f"    entries preview: {entries_text[:120]}...")
        print(f"\n[DRY RUN complete — no output written]")
        return

    # ── Initialize Anthropic client ───────────────────────────
    client = None
    if not args.skip_ai:
        try:
            import anthropic
            import os
            api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("\nERROR: No API key. Use --api-key or set ANTHROPIC_API_KEY.")
                print("       Use --skip-ai to run without API calls.")
                sys.exit(1)
            client = anthropic.Anthropic(api_key=api_key)
            print(f"\n  Using claude-haiku for batch generation ({len(to_patch)} items).")
        except ImportError:
            print("ERROR: anthropic not installed. Run: pip install anthropic")
            sys.exit(1)

    # ── Build lookup by name for fast patching ────────────────
    item_by_name = {item["name"]: item for item in items}

    # ── Process ───────────────────────────────────────────────
    print(f"\nPatching {len(to_patch)} items...")
    ai_count     = 0
    fallback_count = 0
    error_count  = 0

    for i, item in enumerate(to_patch):
        name = item["name"]
        entries_text = entries_to_text(item["entries"])

        if args.skip_ai or not client:
            stats  = entries_text[:150].rstrip() + ("…" if len(entries_text) > 150 else "")
            flavor = item.get("flavor") or "A remarkable item."
            fallback_count += 1
        else:
            try:
                # Keep existing flavor if it's not a placeholder
                existing_flavor = item.get("flavor", "")
                is_placeholder_flavor = (
                    not existing_flavor
                    or existing_flavor.lower() in (
                        "a remarkable item.", "a remarkable item of considerable power.",
                        "a remarkable item", ""
                    )
                )

                stats, new_flavor = generate_stats_and_flavor(client, name, entries_text)
                flavor = new_flavor if is_placeholder_flavor else existing_flavor
                ai_count += 1

                # Progress update every 25 items
                if (i + 1) % 25 == 0:
                    print(f"  [{i+1}/{len(to_patch)}] {name[:50]}")

                # Gentle rate limiting — haiku is fast but let's not hammer
                if (i + 1) % 50 == 0:
                    time.sleep(1)

            except Exception as e:
                print(f"  ERROR on '{name}': {e}")
                stats  = entries_text[:150].rstrip() + "…"
                flavor = item.get("flavor") or "A remarkable item."
                error_count += 1
                fallback_count += 1

        # Patch in-place in the item dict
        item_by_name[name]["stats"]  = stats
        item_by_name[name]["flavor"] = flavor
        # Mark that this was patched (useful for QA)
        item_by_name[name]["_statsPatchedByScript"] = True

    # ── Write output ─────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"PATCH COMPLETE")
    print(f"{'='*50}")
    print(f"  Items patched:       {len(to_patch)}")
    print(f"    AI generated:      {ai_count}")
    print(f"    Fallback/skip-ai:  {fallback_count}")
    print(f"    Errors:            {error_count}")
    print(f"  Items unchanged:     {len(items) - len(to_patch)}")
    print(f"  Items still pending: {len(no_entries)}")
    if no_entries:
        print(f"    (these have no entries data to generate from)")

    # Verify no placeholders remain in patched items
    still_placeholder = sum(
        1 for item in items
        if item.get("_statsPatchedByScript") and is_placeholder(item)
    )
    if still_placeholder:
        print(f"\n  WARNING: {still_placeholder} patched items still have placeholder stats")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    import os
    size_kb = os.path.getsize(args.output) / 1024
    print(f"\n  Written to: {args.output}")
    print(f"  File size:  {size_kb:.0f} KB")
    print(f"\nDone. Load {args.output} into the shop generator as your items database.")


if __name__ == "__main__":
    main()
