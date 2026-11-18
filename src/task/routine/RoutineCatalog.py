"""Read-only access to the domain catalog shipped with the app.

The catalog maps game UI names (regions, weapon types, bosses, zones) to the
1-based serial numbers used by Monster-ww's F2 book navigation. Keeping the
mapping in JSON means new bosses/zones can be added without touching code.
"""

import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[3] / 'assets' / 'data' / 'domain_catalog.json'


@lru_cache(maxsize=1)
def load_catalog():
    with CATALOG_PATH.open(encoding='utf-8') as stream:
        return json.load(stream)


MATERIAL_SERIAL = {
    'Resonator EXP': 1,
    'Weapon EXP': 2,
    'Shell Credit': 3,
}


def simulation_options():
    return list(load_catalog()['simulation']['materials'])


def simulation_regions():
    return list(load_catalog()['simulation']['regions'])


def simulation_serial(region_name, material):
    """1-based serial in the F2 simulation list (4 regions x 3 materials)."""
    regions = simulation_regions()
    region_index = next(
        (index for index, region in enumerate(regions)
         if region_name == region or region_name in region),
        0,
    )
    material_index = MATERIAL_SERIAL.get(material, 3) - 1
    return region_index * len(MATERIAL_SERIAL) + material_index + 1


def forgery_regions():
    return load_catalog()['forgery']['regions']


def forgery_region_names():
    return [region['name'] for region in forgery_regions()]


def forgery_weapon_types():
    return list(load_catalog()['forgery']['weapon_types'])


def forgery_dungeon_name(region_name, weapon_type):
    """Return the in-game dungeon name when known, else a readable fallback."""
    for region in forgery_regions():
        if region_name in [region['name'], *region.get('aliases', [])]:
            name = region.get('dungeons', {}).get(weapon_type)
            if name:
                return name
            order = region.get('weapon_order', forgery_weapon_types())
            index = order.index(weapon_type) + 1 if weapon_type in order else 1
            return f"{region['name']} · {weapon_type} #{index}"
    return f"{region_name} · {weapon_type}"


def forgery_serial(region_name, weapon_type):
    """1-based serial in the F2 forgery list (4 regions x 5 weapon types)."""
    regions = forgery_regions()
    region_index = next(
        (index for index, region in enumerate(regions)
         if region_name in [region['name'], *region.get('aliases', [])]),
        0,
    )
    order = regions[region_index].get('weapon_order', forgery_weapon_types())
    weapon_index = order.index(weapon_type) if weapon_type in order else 0
    return region_index * len(forgery_weapon_types()) + weapon_index + 1


def tacet_zones():
    return list(load_catalog()['tacet']['zones'])


def tacet_serial(zone_name):
    zones = tacet_zones()
    return zones.index(zone_name) + 1 if zone_name in zones else 1


def boss_challenges():
    return [entry['name'] for entry in load_catalog()['boss_challenge']['bosses']]


def boss_challenges_by_region():
    """BOSS names grouped by in-game region, preserving F2 serial order."""
    grouped = {}
    for entry in load_catalog()['boss_challenge']['bosses']:
        grouped.setdefault(entry.get('region', '其他'), []).append(entry['name'])
    return grouped


def boss_challenge_profile(boss_name):
    """FarmEchoTask combat profile name, or 'Other' when unknown."""
    entry = next((entry for entry in load_catalog()['boss_challenge']['bosses']
                  if entry['name'] == boss_name), None)
    return entry.get('profile', 'Other') if entry else 'Other'


def boss_challenge_serial(boss_name):
    bosses = boss_challenges()
    return bosses.index(boss_name) + 1 if boss_name in bosses else 1


def weekly_entries():
    return load_catalog()['weekly']['entries']


def weekly_display_names():
    return [entry['boss'] for entry in weekly_entries()]


def weekly_serial(boss_name):
    entries = weekly_entries()
    return next((index + 1 for index, entry in enumerate(entries)
                 if entry['boss'] == boss_name), 1)


def weekly_dungeon(boss_name):
    entry = next((entry for entry in weekly_entries() if entry['boss'] == boss_name), None)
    return entry['dungeon'] if entry else boss_name
