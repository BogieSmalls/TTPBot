"""Racetime chat summaries using ZORA's published flags/2.0.1.json.

The Z1R decoder already supplies the base settings. Two ZORA controls also
need a value from it to render the same choice as the generator's flag page.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from .flag_summary import base_summary_lines, decode, pack_summary
from .zora import ZORA_FLAGS


SWORD_WORDING = {
    ('Add L4 Sword', 'Level 2'): 'L4 Sword: added (extra item staircase in Level 2)',
    ('Add L4 Sword', 'Level 9'): 'L4 Sword: added (extra floor item in Level 9)',
}

ROOM_PAIR = re.compile(r'(?:^|\s+-\s+|\n)(?:Z1R )?Flags:\s*(\S+)\s+-\s+ZORA(?: Flags)?:\s*(\S+)', re.I)


@lru_cache(maxsize=1)
def flag_spec():
    return json.loads((Path(__file__).with_name('zora_data') / 'flags-2.0.1.json').read_text(encoding='utf-8'))


def room_pair(data):
    for key in ('info_bot', 'info_user'):
        match = ROOM_PAIR.search(data.get(key) or '')
        if match:
            return match.groups()
    return None


def decode_extra(extra, spec):
    version = number = 0
    if extra:
        if not ZORA_FLAGS.fullmatch(extra):
            raise ValueError('Invalid ZORA flags')
        version_text, payload = extra.split('.')
        version = int(version_text)
        if version > spec['zora']['current_version']:
            raise ValueError('Unsupported ZORA flags version')
        alphabet = spec['alphabet']
        for digit in payload:
            number = number * len(alphabet) + alphabet.index(digit)

    values = {}
    for field in spec['zora']['fields']:
        value = field['default']
        if field['added_in_version'] <= version:
            number, value = divmod(number, field['radix'])
        values[field['key']] = value
    if number:
        raise ValueError('Unused digits in ZORA flags')
    return values


def format_zora_summary(flags, extra, registry):
    flags = flags.replace('_', '!')
    extra = '' if extra.lower() == 'none' else extra.replace('_', '!')
    combos, checkboxes = decode(flags)
    base = base_summary_lines(combos, checkboxes)
    spec = flag_spec()
    try:
        values = decode_extra(extra, spec)
    except ValueError:
        return pack_summary(None, base, [f'ZORA flags: {extra} (not readable here)'])

    # The other members of these controls come from the existing Z1R decoder.
    base_fields = {f['key']: f for f in spec['z1r']['fields'] if f.get('page_control')}
    values['C06'] = base_fields['C06']['values'].index(combos['shuffleItemsBox'])
    values['B51'] = ('off', 'on', 'possible').index(checkboxes['printDebug'])
    fields = spec['zora']['fields']
    changed = {field['key'] for field in fields if not field.get('retired') and values[field['key']] != field['default']}
    by_key = {field['key']: field for field in fields}
    by_key.update(base_fields)
    controls = {}
    for control in spec['combined_controls']:
        keys = control['fields']
        if not changed.intersection(keys):
            continue
        match = next((option for option in control['values']
                      if all(values[key] == value for key, value in option['fields'].items())), None)
        first = next(key for key in keys if key not in base_fields)
        if match:
            name, value = control['name'], match['name']
            controls[first] = [SWORD_WORDING.get((name, value), f'{name}: {value}')]
        else:
            controls[first] = [f"{by_key[key]['name']}: {by_key[key]['values'][values[key]]}" for key in keys]
        changed.difference_update(keys)
        if 'C06' in keys:
            base = [line for line in base if not line.startswith('Item Shuffle:')]

    first = []
    for field in fields:
        key = field['key']
        if key in controls:
            first.extend(controls[key])
        elif key in changed:
            first.append(f"{field['name']}: {field['values'][values[key]]}")
    return pack_summary(registry.name_of_pair(flags, extra), base, first)
