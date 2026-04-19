#!/usr/bin/env python3
"""
cleanup_items_db.py
===================
Post-processing cleanup for items_enriched.json:

1. Removes 39 duplicate items that are wrongly-priced algorithm variants
   of correctly-priced originals (Belt of Hill Giant Strength → remove,
   Belt of Giant Strength (Hill) → keep, etc.)

2. Adds image URLs to items that have fluff image data from 5etools.
   Format: https://5e.tools/img/[internal path]

3. Strips internal debug fields (_statsPatchedByScript, _priceNotes)
   from the final output.

4. Fixes the 3 remaining "dormant" placeholder stats with a proper
   note indicating they are dormant vestiges of divergence.

Usage:
    python cleanup_items_db.py \
        --input  items_enriched.json \
        --output items_final.json

Output is ready to bake directly into the shop generator HTML.
"""

import argparse
import json
import re
import os

# ─────────────────────────────────────────────────────────────
# DUPLICATES TO REMOVE
# These are 5etools-named variants of items already in the db
# under the DMPG / original naming convention with correct prices.
# ─────────────────────────────────────────────────────────────
REMOVE_NAMES = {
    # Giant Strength series — originals use "(Hill)" style naming with DMPG prices
    'Belt of Hill Giant Strength',
    'Belt of Stone Giant Strength',
    'Belt of Fire Giant Strength',
    'Belt of Frost Giant Strength',
    'Belt of Cloud Giant Strength',
    'Belt of Storm Giant Strength',
    'Potion of Hill Giant Strength',
    'Potion of Stone Giant Strength',
    'Potion of Fire Giant Strength',
    'Potion of Frost Giant Strength',
    'Potion of Cloud Giant Strength',
    'Potion of Storm Giant Strength',
    # Alchemy Jug colour variants — original is generic "Alchemy Jug"
    'Alchemy Jug (Blue)',
    'Alchemy Jug (Orange)',
    # Armor of Vulnerability damage-type variants
    'Armor of Vulnerability (Bludgeoning)',
    'Armor of Vulnerability (Piercing)',
    'Armor of Vulnerability (Slashing)',
    # Barrier Tattoo size variants — original uses rarity naming
    'Barrier Tattoo (Large)',
    'Barrier Tattoo (Medium)',
    'Barrier Tattoo (Small)',
    # Elemental / Outer Essence Shard element variants
    'Elemental Essence Shard (Air)',
    'Elemental Essence Shard (Earth)',
    'Elemental Essence Shard (Fire)',
    'Elemental Essence Shard (Water)',
    'Outer Essence Shard (Chaotic)',
    'Outer Essence Shard (Evil)',
    'Outer Essence Shard (Good)',
    'Outer Essence Shard (Lawful)',
    # Piwafwi with parenthetical duplicate
    'Piwafwi (Cloak of Elvenkind)',
    # Spell Gem stone-named variants — original uses level naming
    'Spell Gem (Amber)',
    'Spell Gem (Bloodstone)',
    'Spell Gem (Diamond)',
    'Spell Gem (Jade)',
    'Spell Gem (Lapis lazuli)',
    'Spell Gem (Obsidian)',
    'Spell Gem (Quartz)',
    'Spell Gem (Ruby)',
    'Spell Gem (Star ruby)',
    'Spell Gem (Topaz)',
}

# ─────────────────────────────────────────────────────────────
# DORMANT VESTIGE ITEMS — replace bad AI placeholder with honest text
# ─────────────────────────────────────────────────────────────
DORMANT_ITEMS = {
    'Hide of the Feral Guardian (Dormant)':
        'Dormant state. As the item awakens, it grants druidic and natural magic abilities.',
    'Infiltrator\'s Key (Dormant)':
        'Dormant state. As the item awakens, it grants shapeshifting and infiltration abilities.',
    'Verminshroud (Dormant)':
        'Dormant state. As the item awakens, it grants vermin-themed abilities and resistances.',
}

# ─────────────────────────────────────────────────────────────
# IMAGE URL BUILDER
# ─────────────────────────────────────────────────────────────
ETOOLS_IMG_BASE = 'https://5e.tools/img/'

def get_image_url(item):
    """Extract first image URL from fluff data, or None."""
    fluff = item.get('fluff')
    if not fluff:
        return None
    images = fluff.get('images', [])
    if not images:
        return None
    img = images[0]
    href = img.get('href', {})
    if href.get('type') == 'internal':
        path = href.get('path', '')
        if path:
            return ETOOLS_IMG_BASE + path
    elif href.get('type') == 'external':
        return href.get('url')
    return None

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Clean up items_enriched.json for final use')
    parser.add_argument('--input',  required=True, help='Path to items_enriched.json')
    parser.add_argument('--output', required=True, help='Path for cleaned output')
    parser.add_argument('--keep-debug', action='store_true',
                        help='Keep _priceNotes and _statsPatchedByScript fields')
    args = parser.parse_args()

    print(f'Loading {args.input}...')
    with open(args.input, encoding='utf-8') as f:
        items = json.load(f)
    print(f'  Loaded {len(items)} items')

    # ── Step 1: Remove duplicates ─────────────────────────────
    before = len(items)
    items = [i for i in items if i['name'] not in REMOVE_NAMES]
    removed = before - len(items)
    print(f'\nStep 1 — Removed {removed} duplicate items (kept correctly-priced originals)')

    # ── Step 2: Add image URLs ────────────────────────────────
    img_added = 0
    img_skipped_already = 0
    for item in items:
        if item.get('imageUrl'):
            img_skipped_already += 1
            continue
        url = get_image_url(item)
        if url:
            item['imageUrl'] = url
            img_added += 1
    print(f'Step 2 — Added image URLs: {img_added} new, {img_skipped_already} already had one')

    # ── Step 3: Fix dormant placeholder stats ─────────────────
    dormant_fixed = 0
    for item in items:
        if item['name'] in DORMANT_ITEMS:
            item['stats'] = DORMANT_ITEMS[item['name']]
            dormant_fixed += 1
    print(f'Step 3 — Fixed {dormant_fixed} dormant vestige placeholder stats')

    # ── Step 4: Strip debug fields ────────────────────────────
    debug_fields = ['_statsPatchedByScript', '_priceNotes']
    stripped = 0
    if not args.keep_debug:
        for item in items:
            for field in debug_fields:
                if field in item:
                    del item[field]
                    stripped += 1
        print(f'Step 4 — Stripped {stripped} debug field instances')
    else:
        print(f'Step 4 — Keeping debug fields (--keep-debug set)')

    # ── Summary ───────────────────────────────────────────────
    total = len(items)
    has_image = sum(1 for i in items if i.get('imageUrl'))
    has_stats = sum(1 for i in items if i.get('stats') and 'placeholder' not in (i.get('stats','').lower()))
    algo_priced = sum(1 for i in items if i.get('priceSource') == 'algorithm')
    verified = sum(1 for i in items if i.get('verified'))

    print(f'\n{"="*50}')
    print(f'FINAL DATABASE STATS')
    print(f'{"="*50}')
    print(f'  Total items:          {total}')
    print(f'  Items with images:    {has_image} ({has_image*100//total}%)')
    print(f'  Items with stats:     {has_stats}')
    print(f'  Verified prices:      {verified}')
    print(f'  Algorithm prices:     {algo_priced}')

    from collections import Counter
    rdist = Counter(i.get('rarity', 0) for i in items)
    rnames = ['Common', 'Uncommon', 'Rare', 'Very Rare', 'Legendary']
    print(f'\n  By rarity:')
    for r in range(5):
        print(f'    {rnames[r]}: {rdist[r]}')

    # Check for any remaining bad stats
    bad_stats = [i for i in items if any(
        p in (i.get('stats','') or '').lower()
        for p in ['see official source', 'dormant state benefits not specified', 'awaiting full description']
    )]
    if bad_stats:
        print(f'\n  WARNING: {len(bad_stats)} items still have placeholder stats:')
        for i in bad_stats:
            print(f'    {i["name"]}')
    else:
        print(f'\n  ✓ No placeholder stats remaining')

    # ── Write output ──────────────────────────────────────────
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(args.output) / 1024
    print(f'\n  Written to: {args.output}')
    print(f'  File size:  {size_kb:.0f} KB')
    print(f'\nDone. Bake {args.output} into the shop generator using bake_shop_v6.py.')


if __name__ == '__main__':
    main()
