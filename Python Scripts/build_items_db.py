#!/usr/bin/env python3
"""
build_items_db.py
=================
Merges our items.json with 5etools items.json, enriches existing items with
5etools fields, prices new items via the DMPG algorithm, and generates
stats shorthand + flavor text via the Anthropic API.

Usage:
    python build_items_db.py \
        --api-key sk-ant-... \
        --our-items items.json \
        --5etools-items 5etools_items.json \
        --fluff-items fluff-items.json \
        --output items_enriched.json \
        [--limit 50]          # process only N new items (for testing)
        [--skip-ai]           # skip AI generation, use placeholder text
        [--dry-run]           # print stats without writing output

Requirements:
    pip install anthropic
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

# Source books we consider "DMPG-covered" (from the guide's abbreviations)
DMPG_SOURCES = {
    "DMG","BGDIA","CM","CS","ERLW","FTD","GS","GGR","HDQ","IDRF",
    "IMR","LLK","LMP","MTF","MOT","OA","PA","RT","SKT","SCC",
    "TYP","TCE","TA","VGM","WDH","WDMM","WBW","XGE","PHB","SCAG",
    "EGW","AI","GGR","SAC","PotA","BGDA"
}

# Sources to skip entirely (3rd party, future, etc.)
SKIP_SOURCES = {"XDMG","XPHB","UA","UAS","UAS2","PSI","PSZ","PSA","PSK","PSX"}

# Types to skip (vehicles, mounts, trade goods, mundane gear, treasure)
SKIP_TYPES = {"VEH","SHP","AIR","MNT","TG","$","$A","$G","$C","FD","TAH","TB","SPC"}

# Types that map to our category system
TYPE_TO_CATS = {
    "P":   ["potions"],
    "SC":  ["scrolls"],
    "A":   ["ammo"],
    "M":   ["weapons-any", "weapons-melee"],
    "R":   ["weapons-any", "weapons-ranged"],
    "HA":  ["armor"],
    "MA":  ["armor"],
    "LA":  ["armor"],
    "S":   ["shields"],
    "RD":  ["rods"],
    "WD":  ["wands"],
    "INS": ["instruments"],
    "RG":  ["rings"],
    "SCF": ["wondrous-other"],  # spellcasting focus — refined below
    "GS":  ["wondrous-other"],  # gaming set
    "T":   ["wondrous-other"],  # tools
    "AT":  ["wondrous-other"],  # artisan tools
    "OTH": ["wondrous-other"],
    "G":   ["wondrous-other"],  # adventuring gear
}

# 5etools rarity string → our 0-4 integer
RARITY_MAP = {
    "common": 0,
    "uncommon": 1,
    "rare": 2,
    "very rare": 3,
    "legendary": 4,
    "artifact": 4,      # treat artifacts as legendary for pricing
    "unknown (magic)": 1,  # default to uncommon
    "unknown": 0,
    "varies": 1,
    "none": 0,
    "?": 0,
}

# DMPG pricing algorithm constants
RARITY_BASE = {0: 75, 1: 350, 2: 3500, 3: 11000, 4: 60000}

CONSUMABLE_MULT = {0: 0.50, 1: 0.35, 2: 0.20, 3: 0.12, 4: 0.10}

SCROLL_PRICES = {0: 15, 1: 25, 2: 150, 3: 400, 4: 800, 5: 1500,
                 6: 2000, 7: 3500, 8: 5000, 9: 20000}

WEAPON_BONUS_PRICE = {"+1": 500, "+2": 2500, "+3": 15000}
ARMOR_BONUS_PRICE  = {"+1": 3500, "+2": 20000, "+3": 51000}
SHIELD_BONUS_PRICE = {"+1": 450, "+2": 4000, "+3": 22000}
SPELL_BONUS_PRICE  = {"+1": 425, "+2": 4250, "+3": 14500}

# Staff names (to trigger staff category)
STAFF_KEYWORDS = ["staff of", "blackstaff", "dragonstaff"]

# ─────────────────────────────────────────────────────────────
# UTILITY: CLEAN 5ETOOLS TAG SYNTAX FROM TEXT
# ─────────────────────────────────────────────────────────────

TAG_RE = re.compile(r'\{@\w+\s([^}|]+)(?:\|[^}]*)?\}')
SIMPLE_TAG_RE = re.compile(r'\{@\w+\}')

def clean_tags(text):
    """Remove 5etools {@tag text|source} markup, keeping the display text."""
    if not isinstance(text, str):
        return str(text) if text else ""
    text = TAG_RE.sub(r'\1', text)
    text = SIMPLE_TAG_RE.sub('', text)
    return text.strip()

def entries_to_text(entries, max_chars=2000):
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
                parts.extend(_flatten_entries(entry.get("entries", [])))
            elif t == "list":
                for item in entry.get("items", []):
                    if isinstance(item, str):
                        parts.append("• " + clean_tags(item))
                    elif isinstance(item, dict):
                        n = item.get("name","")
                        e = " ".join(clean_tags(x) for x in item.get("entries",[]) if isinstance(x,str))
                        parts.append(f"• {n}: {e}" if n else f"• {e}")
            elif t == "table":
                parts.append("[Table omitted]")
            elif t == "quote":
                pass  # skip flavor quotes
    text = " ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text

def _flatten_entries(entries):
    out = []
    for e in entries:
        if isinstance(e, str):
            out.append(clean_tags(e))
        elif isinstance(e, dict) and "entries" in e:
            out.extend(_flatten_entries(e["entries"]))
    return out

# ─────────────────────────────────────────────────────────────
# CATEGORY MAPPING
# ─────────────────────────────────────────────────────────────

def infer_cats(item5e):
    """Map a 5etools item to our cats[] list."""
    cats = set()
    name = item5e.get("name", "").lower()
    raw_type = item5e.get("type", "") or ""
    base_type = raw_type.split("|")[0].upper()
    wondrous = item5e.get("wondrous", False)
    tattoo = item5e.get("tattoo", False)

    # Direct type mappings
    if base_type in TYPE_TO_CATS:
        for c in TYPE_TO_CATS[base_type]:
            cats.add(c)

    # Staff detection (some staves are type M or OTH)
    if any(k in name for k in STAFF_KEYWORDS) or item5e.get("staff"):
        cats.discard("weapons-any")
        cats.discard("weapons-melee")
        cats.add("staffs")

    # Wondrous items — infer slot from name keywords
    if wondrous or base_type in ("SCF", "OTH", "G", "GS", "T", "AT", "") or not base_type:
        slot = infer_wondrous_slot(name, item5e)
        cats.add(slot)
        cats.discard("wondrous-other")  # replace placeholder if slot found

    # Tattoos
    if tattoo or "tattoo" in name:
        cats.discard("wondrous-other")
        cats.add("tattoos")

    # Scrolls: spellwrought tattoos are both scrolls AND tattoos
    if base_type == "SC" and "tattoo" in name:
        cats.add("tattoos")

    # Spellbooks (arcane grimoires, wizard spell books, chronicles)
    spellbook_words = ["grimoire", "spellbook", "chronicle", "manuscript",
                       "codex", "primer", "compendium", "archive", "treatise",
                       "libram", "verses", "tome of the stilled"]
    if any(w in name for w in spellbook_words):
        cats.discard("wondrous-other")
        cats.add("spellbooks")

    # Gems (spell gems, ioun stones)
    if "spell gem" in name or "ioun stone" in name or "gem of" in name:
        cats.discard("wondrous-other")
        cats.add("gems")

    # Ranged weapons
    if base_type == "R":
        cats.add("weapons-any")
        cats.add("weapons-ranged")
        cats.discard("weapons-melee")

    # Make sure melee weapons also get weapons-any
    if "weapons-melee" in cats:
        cats.add("weapons-any")

    return sorted(cats) if cats else ["wondrous-other"]


SLOT_KEYWORDS = {
    "wondrous-head":      ["helm", "hat", "cap", "circlet", "crown", "headband",
                           "ioun stone", "dread helm", "propeller helm",
                           "peregrine mask", "knave's eye patch"],
    "wondrous-eyes":      ["goggles", "eyes of", "mask of the beast", "mast of the beast",
                           "ersatz eye", "finder's goggles", "eye patch"],
    "wondrous-neck":      ["amulet", "necklace", "brooch", "medallion", "periapt",
                           "scarab", "pendant", "holy symbol", "platinum scarf"],
    "wondrous-shoulders": ["cloak", "cape", "mantle", "wings of", "piwafwi",
                           "nature's mantle"],
    "wondrous-armswrists":["bracers", "bracelet", "bracer", "arcane propulsion arm",
                           "illusionist's bracers"],
    "wondrous-hands":     ["gauntlets", "gloves", "claws of", "prosthetic limb",
                           "living gloves"],
    "wondrous-waist":     ["belt of", "girdle", "sash", "dragonhide belt"],
    "wondrous-body":      ["robe", "vestments", "shiftweave", "glamerweave",
                           "wingwear", "lord's ensemble", "mizzium apparatus",
                           "clothes of mending"],
    "wondrous-feet":      ["boots", "slippers", "horseshoes", "winged boots",
                           "stein rune boots", "ise rune boots", "vind rune boots"],
}

def infer_wondrous_slot(name, item5e):
    """Return the most specific wondrous slot category for an item."""
    for slot, keywords in SLOT_KEYWORDS.items():
        if any(k in name for k in keywords):
            return slot
    return "wondrous-other"

# ─────────────────────────────────────────────────────────────
# ATTUNEMENT STRING
# ─────────────────────────────────────────────────────────────

def format_attune(item5e):
    """Convert 5etools reqAttune to our attune string."""
    req = item5e.get("reqAttune")
    if not req:
        return "No"
    if req is True or req == "true":
        return "Yes"
    if isinstance(req, str):
        r = req.strip().lower()
        if r in ("true", "yes", ""):
            return "Yes"
        if r == "optional":
            return "Yes (optional)"
        # Clean up "by a/an X" → "Yes (X)"
        r = re.sub(r'^by an?\s+', '', r)
        return f"Yes ({r})"
    return "Yes"

# ─────────────────────────────────────────────────────────────
# PRICING ALGORITHM
# ─────────────────────────────────────────────────────────────

def compute_price(item5e, rarity_int):
    """
    Apply the reverse-engineered DMPG algorithm to compute a base price.
    Returns (price_gp, price_source, notes)
    """
    name = item5e.get("name", "").lower()
    raw_type = (item5e.get("type") or "").split("|")[0].upper()
    notes = []

    # ── HARD-CODED SERIES ──────────────────────────────────────

    # Spell scrolls — independent curve by spell level
    scroll_lvl = item5e.get("spellScrollLevel")
    if raw_type == "SC" and scroll_lvl is not None:
        price = SCROLL_PRICES.get(int(scroll_lvl), SCROLL_PRICES[9])
        return price, "algorithm", f"spell scroll level {scroll_lvl}"

    # Bonus weapon/armor/shield/spell series
    bonus_w  = item5e.get("bonusWeapon")
    bonus_a  = item5e.get("bonusAc")
    bonus_sp = item5e.get("bonusSpellAttack")
    bonus_sv = item5e.get("bonusSavingThrow")

    if bonus_w and bonus_w in WEAPON_BONUS_PRICE:
        # Pure +X weapon (no extra effects)
        has_extras = any([item5e.get("attachedSpells"), item5e.get("charges"),
                          item5e.get("resist"), item5e.get("ability")])
        if not has_extras:
            price = WEAPON_BONUS_PRICE[bonus_w]
            notes.append(f"weapon {bonus_w} ladder")
            # Weapons with spell attack bonus too get the spell bonus price instead
            if bonus_sp:
                price = SPELL_BONUS_PRICE.get(bonus_sp, price)
                notes.append(f"+ spell {bonus_sp}")
            return price, "algorithm", ", ".join(notes)

    if bonus_a and bonus_a in ARMOR_BONUS_PRICE and not bonus_w:
        if raw_type in ("HA","MA","LA"):
            price = ARMOR_BONUS_PRICE[bonus_a]
            return price, "algorithm", f"armor {bonus_a} ladder"
        if raw_type == "S":
            price = SHIELD_BONUS_PRICE.get(bonus_a, ARMOR_BONUS_PRICE[bonus_a])
            return price, "algorithm", f"shield {bonus_a} ladder"

    if bonus_sp and not bonus_w and not bonus_a:
        price = SPELL_BONUS_PRICE.get(bonus_sp, RARITY_BASE[rarity_int])
        notes.append(f"spell attack {bonus_sp} ladder")
        return price, "algorithm", ", ".join(notes)

    # Ability score items — fixed values
    ability = item5e.get("ability", {})
    if ability:
        static = ability.get("static", {})
        if static:
            # Override-type (set to fixed value)
            stat = list(static.keys())[0]
            val = list(static.values())[0]
            if stat == "con":
                return 4000, "algorithm", f"ability score override CON→{val}"
            else:
                return 450, "algorithm", f"ability score override {stat}→{val}"
        else:
            # Increase-type (+2 to a stat)
            # Check if it has a max cap (Ioun Stones) or not (Manuals/Tomes)
            is_manual = any(w in name for w in ["manual", "tome of clear", "tome of leadership",
                                                  "tome of understanding", "tome of gainful",
                                                  "tome of quickness", "tome of bodily"])
            if is_manual:
                return 36000, "algorithm", "ability score +2 uncapped (manual/tome)"
            else:
                return 8000, "algorithm", "ability score +2 capped (ioun pattern)"

    # Ammunition — per piece pricing
    if raw_type == "A":
        if bonus_w in WEAPON_BONUS_PRICE:
            ammo_prices = {"+1": 50, "+2": 250, "+3": 1250}
            return ammo_prices.get(bonus_w, 50), "algorithm", f"ammo {bonus_w}"
        return 25, "algorithm", "basic ammo"

    # ── CONSUMABLE BASE ────────────────────────────────────────
    is_consumable = raw_type in ("P",)  # Potions/oils
    is_scroll = raw_type == "SC"
    is_tattoo = item5e.get("tattoo", False)

    if is_consumable:
        base = RARITY_BASE[rarity_int] * CONSUMABLE_MULT[rarity_int]
        # Adjust for power level within consumable tier
        effect_mult = estimate_consumable_power(item5e, name)
        price = round(base * effect_mult)
        # Round to nearest clean number
        price = round_price(price)
        notes.append(f"consumable × {CONSUMABLE_MULT[rarity_int]:.2f} × effect {effect_mult:.1f}")
        return price, "algorithm", ", ".join(notes)

    # ── PERMANENT ITEM BASE ────────────────────────────────────
    base = RARITY_BASE[rarity_int]

    # Category modifier
    cat_mult = 1.0
    if raw_type == "RG":
        cat_mult = 1.5  # Ring slot premium
        notes.append("ring slot ×1.5")

    # Effect modifier
    effect_mult = estimate_permanent_power(item5e, name, raw_type)
    notes.append(f"effect ×{effect_mult:.2f}")

    price = round(base * cat_mult * effect_mult)
    price = round_price(price)

    # Flag flight items (always outside normal range)
    if has_flight(item5e, name):
        notes.append("FLIGHT PREMIUM applied")

    return price, "algorithm", ", ".join(notes)


def estimate_consumable_power(item5e, name):
    """Power multiplier for consumable items (potions/oils)."""
    # Healing potions — well-known prices
    if "healing" in name:
        return 1.0
    # Flying potion
    if "flying" in name or "flight" in name:
        return 2.5
    # Invisibility
    if "invisib" in name:
        return 2.0
    # Giant strength
    if "giant strength" in name:
        if "storm" in name:
            return 8.0
        if "cloud" in name:
            return 2.0
        return 1.0
    # Speed / heroism / invulnerability
    if any(w in name for w in ["speed", "heroism", "invulnerability"]):
        return 1.5
    # Default: standard power
    return 1.0


def has_flight(item5e, name):
    """Check if item grants flight."""
    flight_words = ["fly", "flight", "flying", "winged", "broom", "carpet of flying",
                    "wings", "levitat"]
    name_hit = any(w in name for w in flight_words)
    speed = item5e.get("modifySpeed", {})
    has_fly_speed = isinstance(speed, dict) and "fly" in speed
    return name_hit or has_fly_speed


def estimate_permanent_power(item5e, name, raw_type):
    """
    Power multiplier for permanent items.
    Uses item properties to estimate where in the rarity range it falls.
    """
    # Flight — massive premium
    if has_flight(item5e, name):
        if "unlimited" in name or "speed" in name:
            return 8.0  # unlimited fly speed
        return 5.0  # limited flight

    # Invisibility on demand
    if "invisib" in name and raw_type not in ("P", "SC"):
        return 4.0

    # Teleportation
    if any(w in name for w in ["teleport", "planes", "blink", "ethereal"]):
        return 2.5

    # Scrying / detection
    if any(w in name for w in ["crystal ball", "gem of seeing", "true seeing"]):
        return 3.0

    # Elemental summoning
    if "commanding" in name and "elemental" in name:
        return 1.3

    # Wish
    if "wish" in name or "luck blade" in name:
        return 1.8

    # Charges with spells — price by highest attached spell
    attached = item5e.get("attachedSpells", [])
    if attached:
        return 1.2  # spells add modest premium over base; rarity already captures it

    # Charges with recharge (standard wand/staff behavior)
    if item5e.get("charges") and item5e.get("recharge"):
        return 1.0  # standard

    # Resistances
    resists = item5e.get("resist", [])
    if len(resists) >= 2:
        return 1.3
    if len(resists) == 1:
        return 1.1

    # Broad passive bonus (always on)
    bonus_sv = item5e.get("bonusSavingThrow")
    bonus_a  = item5e.get("bonusAc")
    if bonus_sv and bonus_a:
        return 1.3  # both = broad
    if bonus_sv or bonus_a:
        return 1.1

    # Niche/situational items
    niche_words = ["fish", "water breathing", "cold weather", "winter",
                   "underwater", "disguise", "manta ray", "sewers"]
    if any(w in name for w in niche_words):
        return 0.7

    # Cursed
    if item5e.get("curse") or "cursed" in name:
        return 0.8

    # Cosmetic/trivial
    cosmetic_words = ["billow", "many fashion", "gleaming", "smoldering",
                      "conducting", "pyrotechnic", "scowl", "smile"]
    if any(w in name for w in cosmetic_words):
        return 0.4

    return 1.0


def round_price(price):
    """Round price to a clean number appropriate to its magnitude."""
    if price < 50:
        return max(15, round(price / 5) * 5)
    if price < 200:
        return round(price / 25) * 25
    if price < 1000:
        return round(price / 50) * 50
    if price < 5000:
        return round(price / 100) * 100
    if price < 20000:
        return round(price / 500) * 500
    if price < 100000:
        return round(price / 1000) * 1000
    return round(price / 5000) * 5000

# ─────────────────────────────────────────────────────────────
# ANTHROPIC AI GENERATION
# ─────────────────────────────────────────────────────────────

def generate_stats_and_flavor(client, item_name, entries_text, existing_stats=None):
    """
    Use Claude to generate:
    1. A shorthand stats string (≤25 words, mechanics-focused)
    2. A one-line flavor string (evocative, slightly wry)
    Returns (stats, flavor)
    """
    if existing_stats:
        # Already have stats — just generate flavor
        prompt = f"""Item: {item_name}
Full description: {entries_text[:800]}

Write ONE evocative, slightly wry flavor sentence for this magic item (15 words max).
It should feel like a world-weary merchant's private note about the item.
Output ONLY the flavor sentence, nothing else."""
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role":"user","content":prompt}]
        )
        flavor = resp.content[0].text.strip().strip('"')
        return existing_stats, flavor

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

Output ONLY the JSON object, no other text."""

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role":"user","content":prompt}]
            )
            text = resp.content[0].text.strip()
            # Strip markdown code fences if present
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'\s*```$', '', text)
            data = json.loads(text)
            return data.get("stats",""), data.get("flavor","")
        except (json.JSONDecodeError, Exception) as e:
            if attempt == 2:
                return f"See official source for full description.", "A remarkable item."
            time.sleep(1)

# ─────────────────────────────────────────────────────────────
# DEDUPLICATION / MATCHING
# ─────────────────────────────────────────────────────────────

def build_our_lookup(our_items):
    """Build name→item lookup from our items (lowercase normalized)."""
    lookup = {}
    for item in our_items:
        key = normalize_name(item["name"])
        lookup[key] = item
    return lookup

def normalize_name(name):
    """Normalize item name for matching."""
    n = name.lower().strip()
    # Handle common variants
    n = n.replace("'", "'").replace("'", "'")
    n = re.sub(r'\s+', ' ', n)
    return n

def find_match(item5e, our_lookup):
    """Find our item that matches a 5etools item. Returns our_item or None."""
    name = normalize_name(item5e.get("name", ""))
    return our_lookup.get(name)

# ─────────────────────────────────────────────────────────────
# ITEM FILTERING
# ─────────────────────────────────────────────────────────────

def should_include(item5e):
    """Return True if we want this 5etools item in our database."""
    source = item5e.get("source", "")
    raw_type = (item5e.get("type") or "").split("|")[0].upper()

    # Skip reprints (XDMG etc) — prefer original source
    if source in SKIP_SOURCES:
        return False

    # Skip mundane/vehicle/treasure types
    if raw_type in SKIP_TYPES:
        return False

    # Skip sentient items (DMPG doesn't cover them)
    if item5e.get("sentient"):
        return False

    # Skip artifacts (too unique, no standard price)
    if item5e.get("rarity") == "artifact":
        return False

    # Skip "varies" rarity (item groups — handled via itemGroup)
    if item5e.get("rarity") == "varies":
        return False

    # Skip _copy items (internal references)
    if "_copy" in item5e:
        return False

    # Skip items with no rarity (pure mundane gear like rope, rations, etc.)
    rarity = item5e.get("rarity", "none")
    if rarity == "none":
        # Only include if it has a magic bonus or is wondrous
        is_magic = (item5e.get("wondrous") or item5e.get("bonusWeapon")
                    or item5e.get("bonusAc") or item5e.get("charges")
                    or item5e.get("reqAttune"))
        if not is_magic:
            return False

    return True

# ─────────────────────────────────────────────────────────────
# WEAPON INSTANTIATION (HYBRID APPROACH)
# ─────────────────────────────────────────────────────────────

# When a generic "+1 Weapon (any)" entry exists in our file,
# we keep it consolidated. The 5etools file has per-weapon-type entries
# which we skip (they duplicate our generic). We mark them for the
# shop generator to instantiate at display time.

GENERIC_WEAPON_PATTERNS = [
    r'^\+[123] weapon',
    r'^\+[123] ammunition',
    r'vicious weapon',
    r'weapon of warning',
    r'hellfire weapon',
    r'ild rune weapon',
    r"dragon's wrath weapon",
]

def is_specific_weapon_variant(item5e, our_lookup):
    """
    True if this 5etools item is a specific weapon type that we handle
    generically (e.g., '+1 Longsword' when we have '+1 Weapon (any)').
    """
    name = normalize_name(item5e.get("name",""))
    baseItem = item5e.get("baseItem","")
    # If name already matches one of our items exactly, it's NOT a variant
    if name in our_lookup:
        return False
    # Check if it's a specific instantiation of a generic pattern
    for pat in GENERIC_WEAPON_PATTERNS:
        if re.search(pat, name):
            return True
    # Named weapons with baseItem are specific (e.g., Flame Tongue longsword variant)
    if baseItem and item5e.get("type","").split("|")[0] in ("M","R"):
        generic_name = re.sub(r'\s*\([^)]+\)\s*$', '', item5e.get("name","")).strip()
        if normalize_name(generic_name) in our_lookup:
            return True
    return False

# ─────────────────────────────────────────────────────────────
# SOURCE FORMATTING
# ─────────────────────────────────────────────────────────────

def format_source(item5e):
    """Format source as 'ABBREV page'."""
    src = item5e.get("source","?")
    page = item5e.get("page")
    if page:
        return f"{src} {page}"
    return src

# ─────────────────────────────────────────────────────────────
# ENRICHMENT: MERGE 5ETOOLS DATA INTO OUR ITEM
# ─────────────────────────────────────────────────────────────

ENRICH_FIELDS = [
    "entries", "type", "wondrous", "weight", "dmg1", "dmg2", "dmgType",
    "property", "weaponCategory", "ac", "bonusWeapon", "bonusWeaponAttack",
    "bonusWeaponDamage", "bonusAc", "bonusSavingThrow", "bonusSpellAttack",
    "bonusSpellSaveDc", "charges", "recharge", "rechargeAmount",
    "resist", "immune", "ability", "attachedSpells", "curse", "poison",
    "poisonTypes", "tattoo", "tier", "range", "staff", "focus", "scfType",
    "containerCapacity", "packContents", "lootTables", "miscTags",
    "modifySpeed", "light", "sentient",
]

def enrich_our_item(our_item, item5e):
    """Add 5etools fields to an existing our-item. Our fields take priority."""
    enriched = dict(our_item)
    for field in ENRICH_FIELDS:
        if field in item5e and field not in enriched:
            enriched[field] = item5e[field]
    # Always update these (5etools has better structured data)
    if "entries" in item5e:
        enriched["entries"] = item5e["entries"]
    if "tier" in item5e and "tier" not in our_item:
        enriched["tier"] = item5e["tier"]
    # Mark as enriched
    enriched["5etoolsSource"] = item5e.get("source","")
    enriched["5etoolsPage"] = item5e.get("page")
    return enriched

# ─────────────────────────────────────────────────────────────
# BUILD NEW ITEM FROM 5ETOOLS
# ─────────────────────────────────────────────────────────────

def build_new_item(item5e, stats, flavor, price, price_source, price_notes):
    """Construct a new item in our schema from 5etools data."""
    rarity_str = item5e.get("rarity", "none")
    rarity_int = RARITY_MAP.get(rarity_str, 0)
    raw_type = (item5e.get("type") or "").split("|")[0].upper()

    new_item = {
        # ── Our core fields ──
        "name":      item5e["name"],
        "rarity":    rarity_int,
        "attune":    format_attune(item5e),
        "source":    format_source(item5e),
        "basePrice": price,
        "cats":      infer_cats(item5e),
        "stats":     stats,
        "flavor":    flavor,
        "verified":  False,
        "priceSource": price_source,
        # ── 5etools enrichment ──
        "5etoolsSource": item5e.get("source",""),
        "5etoolsPage":   item5e.get("page"),
    }

    # Add all enrichment fields that exist
    for field in ENRICH_FIELDS:
        if field in item5e:
            new_item[field] = item5e[field]

    # Add pricing notes for review (can be removed later)
    if price_notes:
        new_item["_priceNotes"] = price_notes

    return new_item

# ─────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build enriched D&D items database")
    parser.add_argument("--api-key",       required=False, help="Anthropic API key")
    parser.add_argument("--our-items",     required=True,  help="Path to our items.json")
    parser.add_argument("--5etools-items", required=True,  help="Path to 5etools items.json",
                        dest="etools_items")
    parser.add_argument("--fluff-items",   required=False, help="Path to fluff-items.json")
    parser.add_argument("--output",        required=True,  help="Output path")
    parser.add_argument("--limit",         type=int,       help="Max new items to AI-process")
    parser.add_argument("--skip-ai",       action="store_true",
                        help="Skip AI generation (use placeholder text)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Print stats only, don't write output")
    args = parser.parse_args()

    # ── Load files ──────────────────────────────────────────
    print("Loading files...")
    with open(args.our_items, encoding="utf-8") as f:
        our_items = json.load(f)
    print(f"  Our items: {len(our_items)}")

    with open(args.etools_items, encoding="utf-8") as f:
        etools_raw = json.load(f)
    etools_items = etools_raw.get("item", etools_raw) if isinstance(etools_raw, dict) else etools_raw
    print(f"  5etools items: {len(etools_items)}")

    fluff_lookup = {}
    if args.fluff_items and os.path.exists(args.fluff_items):
        with open(args.fluff_items, encoding="utf-8") as f:
            fluff_raw = json.load(f)
        fluff_list = fluff_raw.get("itemFluff", fluff_raw) if isinstance(fluff_raw, dict) else fluff_raw
        for f_item in fluff_list:
            key = normalize_name(f_item.get("name",""))
            fluff_lookup[key] = f_item
        print(f"  Fluff items: {len(fluff_lookup)}")

    # ── Initialize Anthropic client ─────────────────────────
    client = None
    if not args.skip_ai:
        try:
            import anthropic
            api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                print("ERROR: No API key provided. Use --api-key or set ANTHROPIC_API_KEY env var.")
                print("       Run with --skip-ai to test without API calls.")
                sys.exit(1)
            client = anthropic.Anthropic(api_key=api_key)
            print("  Anthropic client initialized.")
        except ImportError:
            print("ERROR: anthropic package not installed. Run: pip install anthropic")
            sys.exit(1)

    # ── Build lookup of our items ────────────────────────────
    our_lookup = build_our_lookup(our_items)
    print(f"\nBuilding merged database...")

    # ── Process 5etools items ────────────────────────────────
    enriched_our = {}          # name → enriched our-item
    new_items = []             # new items to add
    skipped = []               # filtered out
    variant_skipped = []       # specific weapon variants (handled generically)
    matched_count = 0
    new_count = 0

    # Deduplicate 5etools by name — prefer original source over reprints
    SOURCE_PRIORITY = ["DMG","PHB","XGE","TCE","FTD","ERLW","GGR","MTF","VGM",
                       "PA","SKT","CS","TA","HDQ","RT","WBW","MOT","IDRF",
                       "WDMM","WDH","BGDA","TYP","LMP","LLK","GS","IMR","OA",
                       "SCC","CM","AI","EGW","SCAG","SAC"]

    def source_priority(source):
        try:
            return SOURCE_PRIORITY.index(source)
        except ValueError:
            return len(SOURCE_PRIORITY)

    seen_names = {}
    for item in etools_items:
        name = normalize_name(item.get("name",""))
        source = item.get("source","")
        if name not in seen_names:
            seen_names[name] = item
        else:
            # Keep higher-priority source
            existing_src = seen_names[name].get("source","")
            if source_priority(source) < source_priority(existing_src):
                seen_names[name] = item

    deduped_etools = list(seen_names.values())
    print(f"  5etools after dedup (prefer original source): {len(deduped_etools)}")

    for item5e in deduped_etools:
        # Filter
        if not should_include(item5e):
            skipped.append(item5e.get("name",""))
            continue

        # Check if it's a specific weapon variant we handle generically
        if is_specific_weapon_variant(item5e, our_lookup):
            variant_skipped.append(item5e.get("name",""))
            continue

        name_key = normalize_name(item5e.get("name",""))

        # Match against our items
        our_match = find_match(item5e, our_lookup)

        if our_match:
            # Enrich existing item
            enriched = enrich_our_item(our_match, item5e)
            # Add fluff if available
            fluff = fluff_lookup.get(name_key)
            if fluff:
                enriched["fluff"] = fluff
            enriched_our[name_key] = enriched
            matched_count += 1
        else:
            # New item — queue for pricing + AI generation
            new_items.append(item5e)
            new_count += 1

    # ── Build final enriched list of our existing items ──────
    # Items in our file not matched by 5etools still get included
    final_items = []
    for our_item in our_items:
        key = normalize_name(our_item["name"])
        if key in enriched_our:
            final_items.append(enriched_our[key])
        else:
            final_items.append(our_item)  # keep as-is, no 5etools match

    print(f"\n  Matched & enriched: {matched_count}")
    print(f"  Skipped (filtered): {len(skipped)}")
    print(f"  Skipped (weapon variants): {len(variant_skipped)}")
    print(f"  New items to process: {new_count}")

    # ── Process new items ────────────────────────────────────
    if args.limit:
        new_items = new_items[:args.limit]
        print(f"  (Limited to {args.limit} new items for this run)")

    ai_count = 0
    placeholder_count = 0
    error_count = 0

    print(f"\nProcessing {len(new_items)} new items...")

    for i, item5e in enumerate(new_items):
        name = item5e.get("name","")
        rarity_str = item5e.get("rarity","none")
        rarity_int = RARITY_MAP.get(rarity_str, 0)

        # Compute price
        price, price_source, price_notes = compute_price(item5e, rarity_int)

        # Get entries text for AI
        entries = item5e.get("entries",[])
        entries_text = entries_to_text(entries) if entries else ""

        # Generate stats + flavor
        if args.skip_ai or not client or not entries_text:
            stats = entries_text[:150] if entries_text else "See official source."
            flavor = "A remarkable item of considerable power."
            placeholder_count += 1
        else:
            try:
                stats, flavor = generate_stats_and_flavor(client, name, entries_text)
                ai_count += 1
                # Rate limiting — ~3 requests/second to be safe
                if (i + 1) % 10 == 0:
                    print(f"    Processed {i+1}/{len(new_items)}...")
                    time.sleep(0.5)
            except Exception as e:
                print(f"    AI error for '{name}': {e}")
                stats = entries_text[:150] if entries_text else "See official source."
                flavor = "A remarkable item."
                error_count += 1
                placeholder_count += 1

        # Add fluff
        fluff = fluff_lookup.get(normalize_name(name))

        # Build new item
        new_item = build_new_item(item5e, stats, flavor, price, price_source, price_notes)
        if fluff:
            new_item["fluff"] = fluff

        final_items.append(new_item)

    # ── Sort final list ──────────────────────────────────────
    final_items.sort(key=lambda x: (x.get("rarity",0), x.get("name","").lower()))

    # ── Stats ────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"FINAL DATABASE STATS")
    print(f"{'='*50}")
    print(f"  Total items:          {len(final_items)}")
    print(f"  From our file:        {len(our_items)}")
    print(f"    - Matched+enriched: {matched_count}")
    print(f"    - No 5etools match: {len(our_items) - matched_count}")
    print(f"  New from 5etools:     {len(new_items)}")
    print(f"    - AI generated:     {ai_count}")
    print(f"    - Placeholders:     {placeholder_count}")
    print(f"    - AI errors:        {error_count}")
    print(f"  Filtered out:         {len(skipped)}")
    print(f"  Weapon variants:      {len(variant_skipped)}")

    rarity_names = ['Common','Uncommon','Rare','Very Rare','Legendary']
    from collections import Counter
    rdist = Counter(i.get("rarity",0) for i in final_items)
    print(f"\n  By rarity:")
    for r in range(5):
        print(f"    {rarity_names[r]}: {rdist[r]}")

    verified = sum(1 for i in final_items if i.get("verified"))
    algo = sum(1 for i in final_items if i.get("priceSource") == "algorithm")
    print(f"\n  Verified prices:   {verified}")
    print(f"  Algorithm prices:  {algo}")

    if args.dry_run:
        print("\n[DRY RUN — no output written]")
        return

    # ── Write output ─────────────────────────────────────────
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(final_items, f, ensure_ascii=False, indent=2)
    print(f"\n  Written to: {args.output}")
    print(f"  File size:  {os.path.getsize(args.output)/1024:.0f} KB")


if __name__ == "__main__":
    main()
