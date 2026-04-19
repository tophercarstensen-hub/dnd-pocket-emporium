# Pocket Emporium Changelog

*(formerly "D&D Shop Generator")*

## v10.6 (2026-04-19)
### Roll20 module handout integration (planning)
- **Cross-referenced 2,676 items in `items_final.json` against 2,076 Roll20 module handout images**:
  - **122 items currently without `imageUrl` have a matching Roll20 handout image** ready to wire in. Notable: Draakhorn (RoT), Constantori's Portrait (KftGV), Dawnbringer (OotA), Antigravity Belt + Daoud's Wondrous Lanthorn (QftIS), Docent (Vecna: Eve of Ruin), Cracked Driftglobe (CM).
  - 124 items that already have images have alternate art available from module handouts — optional upgrade path.
- Report saved at `roll20-export/_item_image_matches.json`.
- Wiring into `imageUrl` pending — blocked on max-res refetch (now complete) and final image-host decision (GitHub vs inline-base64).

## v10.5 (prior to 2026-04-19)
### Tuning pass
- Merchant panel compacted.
- Shop presets regrouped into 7 groups, CSS 2-column layout.
- Random button moved into section header (with stopPropagation on parent collapse).
- Sidebar toolbar with Expand/Collapse All.
- `applyRandomPreset` seeds settlement/shrewdness/wealth/mood via `dispatchEvent` so the UI reflects the chosen preset instead of showing "NaN%".
- Color-contrast fixes: `--text-dim` → `#9088a0`, `--text-secondary` → `#c8c0d4`; group tags brightened to `accent-amber #e8a030` with gold left border.
- Collapse-sidebar-except-stock action keeps `sec-presets` open so the player can still change the preset after rolling.

## v10 (renamed, prior)
### Rename + rebuild
- Renamed app from "D&D Shop Generator" to **Pocket Emporium**. Old version archived at `Old Versions/v9/` and `Old Versions/dnd_shop_generator_v9.html`.
- Filename is unversioned (`pocket_emporium.html`); version lives in topbar text only.
- Emoji rebuild incident: a surrogate-pair emoji (`🏪`, `\ud83c\udfea`) in a Python fix-script caused a UTF-8 write to truncate `pocket_emporium.html` to 0 bytes. Recovered from v9 backup; emoji swapped to BMP-safe `⚜` (`\u269c`).

## v9 and earlier (2025 – early 2026)
Prior versions shipped as `dnd_shop_generator_v2.html` through `v9.html` (archived under `Old Versions/`). Highlights across that run:
- **v9**: UI polish pass — current baseline before v10 rename.
- **v7–v8**: DMPG (Discerning Merchant's Pocket Guide) pricing algorithm integrated; 5etools item DB merged (2,676 items).
- **v6**: Bake pipeline introduced (`bake_shop_v6.py`); items baked into the HTML as a base64-encoded JSON const. Bake script re-runnable on already-baked output.
  - **Bake bug fixed:** `re.sub` was interpreting `\n` in the replacement JSON as newlines, corrupting the embedded constant. Fixed by using a `lambda _m: new_const` replacement so the string passes through untouched.
- **v5 and earlier**: Core shop-generator skeleton, preset system, merchant panel.

## Pipeline notes
- Source of truth: `items_final.json` (2,676 entries; 365 with `imageUrl`, 2,311 without).
- Bake: `python bake_shop_v6.py` reads `pocket_emporium.html` + `items_final.json` → writes `pocket_emporium_baked.html`. Filename stays unversioned; version lives in topbar text.
- Sister app: Combat Forge (see `../Combat Calc/CHANGELOG.md`) — shares the Roll20-export pipeline that populates both apps' databases.
