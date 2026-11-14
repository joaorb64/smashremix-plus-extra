"""Which animation each fighter action plays, with its motion flags (FTMotionDesc: anim file,
command offset, flags), the way Smash Remix's Character.asm macros build them.

Vanilla tables, read from the original ROM (Character.asm's constants): the character struct
table (0x92610, per character: +0x64 param array, +0x68 menu array, +0x6C param count), the
shared action array (0xA45D8, 0x14 bytes per action < 0xDC: halfword >> 6 = its param index),
each character's special action array (0xA6F40 table, actions >= 0xDC).

A Remix character folder's main.asm changes them:
  edit_action_parameters(CHAR, action, animation, command, flags)
  add_new_action_params(CHAR, NAME, action_copy, animation, command, flags)
  edit_menu_action_parameters(CHAR, menu action, animation, command, flags)
-1 keeps the parent's (the base character's) value; a new action without a copy has 0.
"""
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
REMIX_SRC = os.path.normpath(os.path.join(HERE, '..', '..', 'smashremix', 'src'))
ORIGINAL_ROM = os.path.normpath(os.path.join(HERE, '..', '..', 'smashremix', 'roms', 'original.z64'))

STRUCT_TABLE = 0x92610
ACTION_ARRAY_TABLE = 0xA6F40
SHARED_ACTION_ARRAY = 0xA45D8
RAM = 0x80084800
MENU_RAM = 0x80288A20
FIRST_SPECIAL = 0xDC
NO_PARAMS = 0x3FF
CHAR_IDS = dict(MARIO=0, FOX=1, DONKEY=2, DK=2, DONKEY_KONG=2, SAMUS=3, LUIGI=4, LINK=5, YOSHI=6,
                CAPTAIN=7, CAPTAIN_FALCON=7, FALCON=7, KIRBY=8, PIKACHU=9, PIKA=9, JIGGLY=10,
                JIGGLYPUFF=10, PUFF=10, NESS=11)
# bytes of each character's special action array (Character.asm scope action_array_size)
SPECIAL_ARRAY_SIZE = {0: 0xB4, 1: 0x208, 2: 0x258, 3: 0xDC, 4: 0xB4, 5: 0x154, 6: 0x118, 7: 0x17C, 8: 0x67C,
                      9: 0x168, 10: 0x140, 11: 0x1F4}
SCOPE_OF = {0: 'MARIO', 1: 'FOX', 2: 'DK', 3: 'SAMUS', 4: 'LUIGI', 5: 'LINK', 6: 'YOSHI', 7: 'CAPTAIN',
            8: 'KIRBY', 9: 'PIKACHU', 10: 'JIGGLY', 11: 'NESS'}


def _u16(d, o):
    return struct.unpack_from('>H', d, o)[0]


def _u32(d, o):
    return struct.unpack_from('>I', d, o)[0]


class Vanilla:
    """The original ROM's action / motion tables."""

    def __init__(self, rom_path=ORIGINAL_ROM):
        self.rom = open(rom_path, 'rb').read()

    def struct(self, cid):
        return _u32(self.rom, STRUCT_TABLE + 4 * cid) - RAM

    def params(self, cid):
        """[(anim file id, command offset, flags)] of the character's motion table."""
        s = self.struct(cid)
        arr, n = _u32(self.rom, s + 0x64) - RAM, _u32(self.rom, s + 0x6C)
        return [struct.unpack_from('>3I', self.rom, arr + 0xC * i) for i in range(min(n, 2048))]

    def menu(self, cid, count=16):
        s = self.struct(cid)
        arr = _u32(self.rom, s + 0x68) - MENU_RAM
        return [struct.unpack_from('>3I', self.rom, arr + 0xC * i) for i in range(count)]

    def param_index(self, cid, action):
        """An action's index in the character's motion table (None: it has none)."""
        if action < FIRST_SPECIAL:
            o = SHARED_ACTION_ARRAY + 0x14 * action
        else:
            o = _u32(self.rom, ACTION_ARRAY_TABLE + 4 * cid) - RAM + 0x14 * (action - FIRST_SPECIAL)
        i = _u16(self.rom, o) >> 6
        return None if i == NO_PARAMS else i

    def flags(self, cid, action):
        i = self.param_index(cid, action)
        p = self.params(cid)
        return p[i][2] if i is not None and i < len(p) else None


def action_names(src=REMIX_SRC):
    """Remix's Action.asm: {'Action.Name' / 'Action.SCOPE.Name': id} and {(scope, id): name}."""
    path = os.path.join(src, 'Action.asm')
    by_name, by_id = {}, {}
    if not os.path.exists(path):
        return by_name, by_id
    scope = []
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.split('//')[0]
        m = re.match(r'\s*scope\s+(\w+)\s*\{', line)
        if m:
            scope.append(m.group(1))
            continue
        if line.strip().startswith('}'):
            if scope:
                scope.pop()
            continue
        m = re.match(r'\s*constant\s+(\w+)\((0x[0-9A-Fa-f]+|\d+)\)', line)
        if m and scope and scope[0] == 'Action':
            v = int(m.group(2), 0)
            sub = scope[1] if len(scope) > 1 else None
            by_name['.'.join(scope + [m.group(1)])] = v
            by_id.setdefault((sub, v), m.group(1))
    return by_name, by_id


def _args(s):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
            continue
        depth += ch == '('
        depth -= ch == ')'
        cur += ch
    out.append(cur.strip())
    return out


def _num(v):
    try:
        return int(v, 0)
    except (TypeError, ValueError):
        return None


def folder_motions(char_dir, char_name, base_character, vanilla=None):
    """A Remix character folder's animations -> ({file stem: dict(flags, actions: [labels],
    conflicts)}, {action labels main.asm gives an animation}) from its main.asm, -1 flags resolved
    from the base character's vanilla tables. char_name: the folder's name (File.<NAME>_ANIM_<STEM>)."""
    asm = os.path.join(char_dir, 'main.asm')
    stems = {os.path.splitext(x)[0].upper(): os.path.splitext(x)[0]
             for x in os.listdir(os.path.join(char_dir, 'animations'))} \
        if os.path.isdir(os.path.join(char_dir, 'animations')) else {}
    if not os.path.exists(asm) or not stems:
        return {}, set()
    names, by_id = action_names()
    cid = CHAR_IDS.get(str(base_character).upper())
    try:
        van = vanilla or (Vanilla() if cid is not None else None)
    except OSError:
        van = None
    prefix = f'File.{char_name.upper()}_ANIM_'
    text = re.sub(r'//[^\n]*', '', open(asm, encoding='utf-8', errors='replace').read())
    new_flags = {}                       # a new action's flags (add_new_action_params)
    out = {}
    overridden = set()                   # action labels main.asm gives an animation

    def action_id(v):
        n = _num(v)
        return n if n is not None else names.get(v)

    def add(anim, label, flags):
        if _num(anim) != -1:
            overridden.add(label)                       # (its own animation, or another's)
        if not anim.startswith(prefix):
            return
        stem = stems.get(anim[len(prefix):].upper())
        if stem is None:
            return
        e = out.setdefault(stem, dict(flags=None, actions=[], conflicts=[]))
        e['actions'].append(label)
        if flags is None:
            return
        if e['flags'] is None:
            e['flags'] = flags
        elif e['flags'] != flags:
            e['conflicts'].append((label, flags))

    calls = re.findall(r'Character\.(edit_action_parameters|add_new_action_params|edit_menu_action_parameters)'
                       r'\(([^\n]*)\)', text)
    for kind, raw in calls:                                    # new actions first (edits refer to them)
        if kind != 'add_new_action_params':
            continue
        a = _args(raw)
        if len(a) < 6:
            continue
        _, name, copy, anim, _, fl = a[:6]
        flags = _num(fl)
        if flags == -1 or flags is None:
            cp = action_id(copy)
            if cp is not None and cp >= FIRST_SPECIAL and (cp - FIRST_SPECIAL) * 0x14 >= SPECIAL_ARRAY_SIZE.get(cid, 0):
                cp = None
            flags = (van.flags(cid, cp) if van and cp not in (None, -1) else None) or 0
        new_flags[name] = flags
        add(anim, name, flags)
    for kind, raw in calls:
        a = _args(raw)
        if kind == 'add_new_action_params' or len(a) < 5:
            continue
        _, act, anim, _, fl = a[:5]
        flags = _num(fl)
        if kind == 'edit_menu_action_parameters':
            label = f'Menu {_num(act)}' if _num(act) is not None else f'Menu {act}'
            if flags in (None, -1):
                i = _num(act)
                flags = van.menu(cid)[i][2] if van and i is not None and 0 <= i < 16 else None
        else:
            n_ = _num(act)                              # a number: the parent's name for it
            label = (by_id.get((None, n_)) or by_id.get((SCOPE_OF.get(cid), n_)) or f'Action_{n_:03X}'
                     if n_ is not None else act.split('.')[-1])
            if flags in (None, -1):
                short = act.split('.')[-1]
                if '.Action.' in act and not act.startswith('Action.') and short in new_flags:
                    flags = new_flags[short]                    # one of its new actions
                else:
                    aid = action_id(act)
                    if aid is not None and aid >= FIRST_SPECIAL and \
                            (aid - FIRST_SPECIAL) * 0x14 >= SPECIAL_ARRAY_SIZE.get(cid, 0):
                        aid = None                          # (not one of the parent's)
                    flags = van.flags(cid, aid) if van and aid is not None else None
        add(anim, label, flags)
    return out, overridden


def rom_motions(cid, vanilla=None):
    """A vanilla fighter's motions: {param index: dict(file_id, flags, actions)} from the
    original ROM (shared actions and its own, named from Remix's Action.asm)."""
    van = vanilla or Vanilla()
    _, by_id = action_names()
    params = van.params(cid)
    out = {}
    for i, (fid, cmd, flags) in enumerate(params):
        out[i] = dict(file_id=fid, flags=flags, actions=[])
    scope = SCOPE_OF.get(cid)
    for action in range(0, FIRST_SPECIAL + SPECIAL_ARRAY_SIZE.get(cid, 0) // 0x14):
        i = van.param_index(cid, action)
        if i is None or i not in out:
            continue
        nm = by_id.get((None, action)) or by_id.get((scope, action)) or f'Action_{action:03X}'
        out[i]['actions'].append(nm)
    for k, (fid, cmd, flags) in enumerate(van.menu(cid)):
        out[f'menu{k}'] = dict(file_id=fid, flags=flags, actions=[f'Menu {k}'])
    return out
