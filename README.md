# Pocket Emporium

A self-contained D&D 5e shop generator. Single-file HTML app — open `pocket_emporium_baked.html` in any modern browser.

## What it does

- Generates themed merchants (race, gender, shrewdness, settlement type)
- Builds randomized inventories from a 2,676-item database sourced from 5etools + Roll20 adventure modules
- DMPG-style pricing algorithm (Discerning Merchant's Pocket Guide)
- Per-item stat panel with full official descriptions and item art

## Repo contents

```
pocket_emporium.html          # source HTML (edit this)
pocket_emporium_baked.html    # baked output (open in browser)
items_final.json              # item database (2,676 items)
bake_shop_v6.py               # bakes items_final.json into the HTML as a base64 const
shop_art/                     # 108 deduped WebP images, served via jsDelivr CDN
CHANGELOG.md
```

## Workflow

```bash
# Edit pocket_emporium.html
python bake_shop_v6.py --items items_final.json --html pocket_emporium.html --output pocket_emporium_baked.html
# Open pocket_emporium_baked.html in browser
```

## Image hosting

Item art is served via jsDelivr CDN from this repo:

```
https://cdn.jsdelivr.net/gh/tophercarstensen-hub/dnd-pocket-emporium@main/shop_art/<hash>.webp
```

The HTML constructs URLs via `ART_BASE_URL` + the `imagePath` field on each item record.

## Companion app

[Combat Forge](https://github.com/tophercarstensen-hub/dnd-combat-forge) — encounter simulator, shares the Roll20-export data pipeline.
