#!/usr/bin/env python3
"""
bake_shop_v6.py
===============
Embeds items_enriched.json into the shop generator HTML as a
baked-in ITEM_DATA constant, producing a self-contained single-file app.

Usage:
    python3 bake_shop_v6.py \
        --items items_enriched.json \
        --html  dnd_shop_generator_v7.html \
        --output dnd_shop_generator_v7_baked.html

Run this once after build_items_db.py completes.
After baking, open the output HTML directly — no loading required.
"""

import argparse
import json
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description='Bake items into shop generator v7')
    parser.add_argument('--items',  required=True, help='Path to items_enriched.json')
    parser.add_argument('--html',   required=True, help='Path to dnd_shop_generator_v7.html')
    parser.add_argument('--output', required=True, help='Output path for baked HTML')
    args = parser.parse_args()

    # Load items
    print(f'Loading items from: {args.items}')
    with open(args.items, encoding='utf-8') as f:
        items = json.load(f)
    print(f'  Loaded {len(items)} items')

    # Load HTML
    print(f'Loading HTML from: {args.html}')
    with open(args.html, encoding='utf-8') as f:
        src = f.read()
    print(f'  Loaded {len(src)//1024} KB HTML')

    # Build compact JSON
    items_json = json.dumps(items, separators=(',', ':'), ensure_ascii=False)
    new_const = f'const ITEM_DATA={items_json};'

    # Replace placeholder
    if 'const ITEM_DATA = [];' in src:
        src = src.replace('const ITEM_DATA = [];', new_const)
        print(f'  Replaced ITEM_DATA placeholder')
    elif re.search(r'const ITEM_DATA\s*=\s*\[', src):
        # Use a callable to avoid re.sub interpreting backslashes
        # (`\n`, `\\`, `\1` etc.) in the JSON replacement string.
        src = re.sub(r'const ITEM_DATA\s*=\s*\[[\s\S]*?\];',
                     lambda _m: new_const, src)
        print(f'  Replaced existing ITEM_DATA constant')
    else:
        print('ERROR: Could not find ITEM_DATA constant in HTML.')
        print('       Make sure you are using dnd_shop_generator_v6.html')
        sys.exit(1)

    # Write output
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(src)

    size_mb = os.path.getsize(args.output) / 1024 / 1024
    print(f'\nDone!')
    print(f'  Output: {args.output}')
    print(f'  Size:   {size_mb:.1f} MB')
    print(f'  Items:  {len(items)} baked in')
    print(f'\nOpen {args.output} in your browser — items load instantly, no file picker needed.')


if __name__ == '__main__':
    main()
