"""Purge monster stat-block entries that mistakenly leaked into items_final.json.

Detection rules:
  1. Source is a MONSTER_ONLY book (MM, MTOF, VGM, Kobold ToB*, etc.) AND
     the item's name matches an entry in Combat Calc's monsters_final.json.
  2. Source is MONSTER_ONLY and the entry has no basePrice — item
     shouldn't exist.
  3. Source is an adventure book AND name matches a monster AND the
     entry has no entries text AND no itemness signals (attune,
     weaponCategory, wondrous, charges, dmg1, etc.).

Backs up to items_final.json.bak-monster-purge before writing.
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path

ITEMS = Path(__file__).parent / "items_final.json"
MONSTERS = Path(__file__).parent.parent / "Combat Calc" / "monsters_final.json"

MONSTER_ONLY_SOURCES = {
    'MM','XMM','MTOF','MMoM','VGM',
    'KP-ToB','KP-ToB2','KP-ToB3','KP-ToB:2023',
    '3P-MME','MME','CC','FTD',
}
CHECK_SOURCES = {'TftYP','BGDIA','WDH','SKT','GoS','KftGV','CM','LLK','HftT'}

def source_prefix(s: str) -> str:
    return (s or '').split(' ')[0].split(':')[0]


def main() -> None:
    items = json.loads(ITEMS.read_text(encoding='utf-8'))
    monsters = json.loads(MONSTERS.read_text(encoding='utf-8'))
    mnames = {(m.get('name') or '').strip().lower() for m in monsters if m.get('name')}

    keeps: list[dict] = []
    drops: list[dict] = []
    for it in items:
        src = source_prefix(it.get('source',''))
        name_lc = (it.get('name') or '').strip().lower()
        is_mname = name_lc in mnames

        drop = False
        reason = ''
        if src in MONSTER_ONLY_SOURCES and is_mname:
            drop = True; reason = 'monster-only source + name matches monster'
        elif src in MONSTER_ONLY_SOURCES and not it.get('basePrice', 0):
            drop = True; reason = 'monster-only source + no basePrice'
        elif src in CHECK_SOURCES and is_mname:
            has_entries = bool(it.get('entries')) and len(str(it.get('entries'))) > 80
            has_itemness = any(it.get(k) for k in (
                'attune','weaponCategory','wondrous','bonusWeapon','bonusAc',
                'charges','dmg1','mastery','armor','ac'))
            if not has_entries and not has_itemness:
                drop = True; reason = 'adventure source + name matches monster, no itemness'
        if drop:
            it['_dropReason'] = reason
            drops.append(it)
        else:
            keeps.append(it)

    print(f'Total items: {len(items)}')
    print(f'Dropping:    {len(drops)}')
    print(f'Keeping:     {len(keeps)}')
    print()
    for d in drops:
        print(f'  DROP {d.get("name",""):<44} src={d.get("source","")[:18]:<18} reason={d["_dropReason"]}')

    # Backup + write
    bak = ITEMS.with_suffix('.json.bak-monster-purge')
    shutil.copy2(ITEMS, bak)
    ITEMS.write_text(json.dumps(keeps, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\nBackup: {bak}')
    print(f'Wrote:  {ITEMS}')


if __name__ == '__main__':
    main()
