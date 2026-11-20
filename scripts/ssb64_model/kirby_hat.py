#!/usr/bin/env python3
"""Kirby copy-ability hats for extra characters: extra_characters/<Name>/kirby_hat/kirby_hat.bin.

kirby_hat.bin is the game file itself (the build adds it as is): Kirby's body part (game joint 6,
model part 2: every copy-ability hat is Kirby's body + the hat) with the hat drawn after the body,
and optionally the item Kirby holds while the copied special shows its "gun" (the model part on
his hand, game joint 0x11, that Fox's / Wolf's blaster uses: moveset event A0880000 shows it,
A08FFFFF hides it; held items point along the joint's +X).

Edit it in Blender: `ssb64_model.py export extra_characters/<Name> OUT_DIR` also writes
OUT_DIR/kirby_hat/model.blend: Kirby from the ROM (his body, bones and animations; more animation
files: the Action Editor's N64 Animation box) with
the hat as his body's model part 'part<H>_hi_j02' / 'part<H>_lo_j02' and the item as his hand's
'part<I>_hi_j13' / 'part<I>_lo_j13' (Model parts collection, shown; their numbers are in
kirby_hat.json). `ssb64_model.py import OUT_DIR` writes kirby_hat.bin back. Kirby's body in the hat
part is only for reference: the file always gets the ROM's Kirby body (with its materials and
costume scripts) and the hat's own triangles after it.

    python3 kirby_hat.py new HAT_DIR                 # a kirby_hat.bin with no hat (Kirby's body only)
    python3 kirby_hat.py info HAT_DIR/kirby_hat.bin   # the offsets the build uses
    python3 kirby_hat.py preview HAT_DIR/kirby_hat.bin OUT.png

Extra hats: more variants of the hat (Kirby's body + other triangles), e.g. the hat with Meta
Knight's tornado around it, which Kirby's moveset shows for a move by setting his body's model part
(joint 6) to its hat id (KirbyHats.<NAME>_HAT_<n>: moveset event A0300000 | id; actions reset it).
In the Kirby blend: more body parts 'part<V>_hi_j02' / '_lo_j02' after the hat's (V > H), in order.

File layout: ... data ..., then the header the build reads (FTModelParts {dl, mobjsubs,
costume_matanim, main_matanim, flags}: hat hi, hat lo, item hi, item lo (no item: NULL display
lists), then the extra hats' hi, lo), then (with extra hats) their u32 count, the u32 internal
relocation chain head, 'KHAT' ('KHT2' with extra hats).
"""
import json
import os
import shutil
import struct
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # appender root
import model_files as MF  # noqa: E402
import gbi_writer as GW  # noqa: E402
import import_fighter as IF  # noqa: E402
from SSB import N64, SSBtbl  # noqa: E402
from smashremix_extra import ge_bin as ge  # noqa: E402

KIRBY_MAIN, KIRBY_CHAR = 0xE5, 0x148      # vanilla file ids (Remix keeps them; J Kirby shares the character file)
HAT_JOINT = 2                             # DObjDesc index (game joint 6): Kirby's body, which every hat replaces
ITEM_JOINT = 13                           # DObjDesc index (game joint 0x11): what Kirby holds (blaster)
PART_SIZE = MF.MODELPART_SIZE             # FTModelPart {dl, mobjsubs, costume_matanim, main_matanim, flags}
HEADER_SIZE = 4 * PART_SIZE
MAGIC = b'KHAT'
MAGIC_EXTRA = b'KHT2'                      # (with extra hats: their count before the chain head)
BIN = 'kirby_hat.bin'
DEFAULT_ROM = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'smashremix', 'roms', 'original.z64')


# ---------------------------------------------------------------- Kirby from the ROM
def rom_kirby(rom_path):
    """Kirby's main / character files from the ROM -> (model_files fighter, raw character file,
    its internal chain head, the ROM's file table)."""
    import export_model as EX
    entries = SSBtbl.fromROM(N64(open(rom_path, 'rb').read()))
    me, ce = entries[KIRBY_MAIN], entries[KIRBY_CHAR]
    char_data, char_ext = EX.rom_external_data(entries, ce)
    with tempfile.TemporaryDirectory() as tmp:
        mp, cp = os.path.join(tmp, 'main.bin'), os.path.join(tmp, 'character.bin')
        open(mp, 'wb').write(me.extract('data'))
        open(cp, 'wb').write(char_data)
        f = MF.load(mp, cp, me.tbl or 0, me.res or 0, ce.tbl or 0, char_ext)
    return f, ce.extract('data'), ce.tbl or 0, entries


def body_fields(f):
    """Kirby's plain body (joint 2 model part 0): {'hi'/'lo': [dl, mobjsubs, costume_matanim, main_matanim]}."""
    out = {}
    for k, detail in enumerate(('hi', 'lo')):
        got = [f.ptr_main(f.modelparts[HAT_JOINT][0][k]['field'] + o) for o in (0, 4, 8, 0xC)]
        out[detail] = [g[1] if g and g[0] == 'char' else None for g in got]
    if any(out[d][3] is not None for d in out):
        raise SystemExit("Kirby's body part has a main material animation: not supported")
    return out


# ---------------------------------------------------------------- the file
def read(data):
    """kirby_hat.bin -> (chain head, {field: target}, header offset, extra hat count)."""
    data = bytes(data)
    head = struct.unpack_from('>I', data, len(data) - 8)[0] if len(data) >= 8 else 0
    if data[-4:] == MAGIC:
        return head, MF.chain(data, head), len(data) - 8 - HEADER_SIZE, 0
    if data[-4:] == MAGIC_EXTRA:
        n = struct.unpack_from('>I', data, len(data) - 12)[0]
        return head, MF.chain(data, head), len(data) - 12 - HEADER_SIZE - 2 * PART_SIZE * n, n
    raise SystemExit('not a kirby_hat.bin (no KHAT / KHT2 trailer)')


def asm_offsets(data):
    """What KirbyHats.asm needs: {dl_hi, ms_hi, an_hi, dl_lo, ms_lo, an_lo, item} (file offsets;
    item: the {hi, lo} FTModelPart pair, -1 if none) and the chain head."""
    head, ptrs, hdr, n_extra = read(data)

    def part(at):
        return {f'{key}_{detail}': ptrs.get(at + PART_SIZE * k + o, -1)
                for k, detail in enumerate(('hi', 'lo')) for key, o in (('dl', 0), ('ms', 4), ('an', 8))}
    off = part(hdr)
    item = hdr + 2 * PART_SIZE
    off['item'] = item if (item in ptrs or item + PART_SIZE in ptrs) else -1
    off['extras'] = [part(hdr + 4 * PART_SIZE + 2 * PART_SIZE * k) for k in range(n_extra)]
    return off, head


def pack(f, char_raw, char_head, textures, hat, item, costumes, say=print, extra_hats=()):
    """Kirby's ROM body + the hat's triangles (+ the item, + extra hats) -> kirby_hat.bin bytes.
    hat / item / extra_hats[k]: {'hi': object, 'lo': object} (import_fighter.load_gltf-style objects;
    'lo' may be missing: 'hi' is used), materials from `textures` (IF.Materials)."""
    fields = body_fields(f)
    roots = [x for d in fields.values() for x in d if x is not None]
    res = ge.gc(char_raw, roots, head=char_head)            # the body, with what it reaches
    base = bytearray(res.data)
    base += b'\0' * (-len(base) % 8)
    blob = GW.Blob(len(base))
    joints = f.details['hi']['joints']
    W = [j.world for j in joints]
    prologue = next((p for p in (IF.prologue_of(f, j.dl) for j in joints if j.dl is not None) if len(p) >= 16), b'')
    prologue = b''.join(prologue[o:o + 8] for o in range(0, len(prologue), 8)
                        if struct.unpack_from('>I', prologue, o)[0] != 0xE200001C)
    data_at = {}

    def store(data, align=8):
        if data not in data_at:
            data_at[data] = blob.add(data, align)
        return data_at[data]

    import import_object as IO
    mw = IO.ModelWriter(f, textures, blob)
    mw.data_at = data_at                       # (images shared with the DL writer's inline loads)
    scripts = {}

    def mobj(name):
        o = mw.mobjsub(name)
        if name not in scripts:
            sc = textures.get(name).script(costumes)
            scripts[name] = store(sc, 4) if sc else None
        return o, scripts[name]

    def lists(mobjs):
        lst = blob.add(b'\0' * (4 * (len(mobjs) + 1)), 4)
        an = blob.add(b'\0' * (4 * max(len(mobjs), 1)), 4)
        for i, (ms, sc) in enumerate(mobjs):
            blob.ptr(lst + 4 * i, ms)
            if sc is not None:
                blob.ptr(an + 4 * i, sc)
        return lst, an

    header = []                                 # (dl, mobjsub list, script array) per part

    def hat_parts(hat, label):
        out = []
        for detail in ('hi', 'lo'):
            dl, ms, an, _ = fields[detail]
            body_ms = [res.remap[x] for x in (MF.pointer_run(f.char_int, ms) if ms is not None else [])]
            body_an = [f.char_int.get(an + 4 * i) if an is not None else None for i in range(len(body_ms))]
            body_an = [res.remap[x] if x is not None else None for x in body_an]
            o = hat.get(detail) or hat.get('hi')
            body = (o or {}).get('body')        # Kirby's body edited in Blender (e.g. retextured): drawn from
            hat_names = []                      # the blend with his own MObjs (same slots), not the ROM's DL
            if body is not None:
                names = list(body['names'])[:len(body_ms)]
                names += [None] * (len(body_ms) - len(names))
                dls, mats = IF.build_joints([dict(o, tris=body['tris'] + o['tris'])], W, textures, blob, prologue,
                                            store, only_joint=HAT_JOINT, mobj_order={HAT_JOINT: names})
                hat_names = mats[HAT_JOINT][len(body_ms):]
                top = blob.add(GW.g(0xDE000000) + GW.g(0xDF000000), 8)
                blob.ptr(top + 4, dls[HAT_JOINT])
            else:
                cmds = [GW.g(0xDE000000)]                                   # the body, then the hat
                if o is not None and o['tris']:
                    dls, mats = IF.build_joints([o], W, textures, blob, prologue, store, only_joint=HAT_JOINT,
                                                mobj_order={HAT_JOINT: [None] * len(body_ms)})
                    hat_names = mats[HAT_JOINT][len(body_ms):]
                    cmds.append(GW.g(0xDE000000))
                top = blob.add(b''.join(cmds) + GW.g(0xDF000000), 8)
                blob.ptr(top + 4, res.remap[dl])
                if len(cmds) > 1:
                    blob.ptr(top + 12, dls[HAT_JOINT])
            lst, arr = lists(list(zip(body_ms, body_an)) + [mobj(n) for n in hat_names])
            out.append((top, lst, arr))
            say(f'  {label} {detail}: {len(o["tris"]) if o else 0} triangles'
                + (f' + Kirby\'s body from the blend ({len(body["tris"])})' if body is not None else '')
                + (f', {len(hat_names)} MObj(s) after the body\'s {len(body_ms)}' if hat_names else ''))
        return out
    header += hat_parts(hat, 'hat')
    for detail in ('hi', 'lo'):
        o = item.get(detail) or item.get('hi')
        if o is None or not o['tris']:
            header.append((None, None, None))
            continue
        dls, mats = IF.build_joints([o], W, textures, blob, prologue, store, only_joint=ITEM_JOINT)
        names = mats[ITEM_JOINT]
        lst = arr = None
        if names:
            lst, arr = lists([mobj(n) for n in names])
            if not any(scripts[n] is not None for n in names):
                arr = None
        header.append((dls[ITEM_JOINT], lst, arr))
        say(f'  item {detail}: {len(o["tris"])} triangles')
    for k, extra in enumerate(extra_hats):
        header += hat_parts(extra, f'extra hat {k + 1}')
    hdr = blob.add(b'\0' * (PART_SIZE * len(header)), 8)
    for k, targets in enumerate(header):
        for o, t in zip((0, 4, 8), targets):
            if t is not None:
                blob.ptr(hdr + PART_SIZE * k + o, t)
    data = GW.append_blob(base, blob, res.new_head, lambda d, h: MF.chain(d, h))
    if extra_hats:
        data += struct.pack('>II', len(extra_hats), res.new_head) + MAGIC_EXTRA
    else:
        data += struct.pack('>I', res.new_head) + MAGIC
    if len(data) > 0x3FFFC:
        raise SystemExit(f'kirby_hat.bin is {len(data)} bytes: over the 0x3FFFC limit')
    return bytes(data)


def dl_words(data, at, limit=4096):
    """A display list's command words up to its end, pointer halves left out (they differ
    between copies): what tells the ROM body's DL from another."""
    out = []
    o = at
    while o + 8 <= len(data) and len(out) < limit:
        w0 = struct.unpack_from('>I', data, o)[0]
        out.append(w0)
        if w0 >> 24 == 0xDF:
            break
        o += 8
    return out


def kirby_with_hat(rom_path, data):
    """Kirby from the ROM with kirby_hat.bin appended to his character data and its parts added as
    model part variants (hat: joint 2, item: joint 13) -> (fighter, {hat_variant, item_variant,
    body_mobjs}). What the Blender export shows and the import reads back against."""
    f, _, _, _ = rom_kirby(rom_path)
    rom_body = {d: dl_words(f.char, x[0]) for d, x in body_fields(f).items()}
    head, ptrs, hdr, n_extra = read(data)
    base = len(f.char) + (-len(f.char) % 16)
    f.char = bytes(f.char) + bytes(base - len(f.char)) + bytes(data)
    for fld, tgt in ptrs.items():
        f.char_int[base + fld] = base + tgt
    main = bytearray(f.main)
    main += bytes(-len(main) % 8)
    info = {}
    for key, joint, k0 in [('hat', HAT_JOINT, 0), ('item', ITEM_JOINT, 2)] + \
            [(f'extra_hat_{k}', HAT_JOINT, 4 + 2 * k) for k in range(n_extra)]:
        if hdr + PART_SIZE * k0 not in ptrs and hdr + PART_SIZE * (k0 + 1) not in ptrs:
            continue
        pair = []
        for k in (k0, k0 + 1):
            fld = len(main)
            main += bytes(PART_SIZE)
            for o in (0, 4, 8):
                t = ptrs.get(hdr + PART_SIZE * k + o)
                if t is not None:
                    f.main_ext[fld + o] = base + t
            pair.append(dict(field=fld, dl=f.main_ext.get(fld), mobjsubs=f.main_ext.get(fld + 4),
                             scripts=f.main_ext.get(fld + 8), flags=0))   # (costume scripts: Kirby's body colors)
        if joint == HAT_JOINT:
            # does the part draw Kirby's ROM body (its first call = the ROM body's display list) or
            # its own (edited in Blender: pack's 'body')? The import keeps it that way
            for d, k in (('hi', k0), ('lo', k0 + 1)):
                top = ptrs.get(hdr + PART_SIZE * k)
                sub = ptrs.get(top + 4) if top is not None else None
                if sub is not None and dl_words(data, sub) != rom_body[d]:
                    info.setdefault('own_body', {}).setdefault(key, []).append(d)
        f.modelparts.setdefault(joint, [])
        if key.startswith('extra_hat_'):
            info.setdefault('extra_hat_variants', []).append(len(f.modelparts[joint]))
        else:
            info[f'{key}_variant'] = len(f.modelparts[joint])
        f.modelparts[joint].append(pair)
    f.main = bytes(main)
    return f, info


def costumes_of(f):
    """Kirby's costume count: his body color script's frames."""
    an = f.ptr_main(f.modelparts[HAT_JOINT][0][0]['field'] + 8)[1]
    lens = [len(MF.decode_script(f.char, f.char_int[an + 4 * i])) for i in range(4) if an + 4 * i in f.char_int]
    return min(lens) if lens else 4


# ---------------------------------------------------------------- Blender
def export(hat_bin, out_dir, rom_path=DEFAULT_ROM, animations=True, preview=True):
    """OUT_DIR = an export folder of Kirby wearing the hat (ssb64_model.py builds OUT_DIR/model.blend).
    Kirby's high-detail body and skeleton, the hat and item parts (his other parts, low detail and
    electric skeleton left out), costume 0, and all his animations for reference (more: Action
    Editor > N64 Animation > Load Animation .bin)."""
    import export_model as EX
    data = open(hat_bin, 'rb').read()
    f, info = kirby_with_hat(rom_path, data)
    keep = {(HAT_JOINT, info['hat_variant'])}
    if 'item_variant' in info:
        keep.add((ITEM_JOINT, info['item_variant']))
    keep |= {(HAT_JOINT, v) for v in info.get('extra_hat_variants', [])}
    f.modelparts = {j: [pair if (j, v) in keep else [dict(p, dl=None) for p in pair] for v, pair in enumerate(vs)]
                    for j, vs in f.modelparts.items() if any(k[0] == j for k in keep)}
    f.details = {'hi': f.details['hi']}
    f.skeletons = {}
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    shutil.copy(hat_bin, os.path.join(out_dir, 'exported_' + BIN))
    ex = EX.Exporter(f, out_dir, 1)
    if animations:                       # the character's Kirby animations, then every ROM Kirby one
        own = []
        try:
            own = hat_animations(os.path.dirname(os.path.dirname(os.path.abspath(hat_bin))), f)
        except (OSError, ValueError, KeyError) as e:
            print(f'  note: no kirby_hat/animations exported ({e})')
        rom = []
        try:
            _, _, _, entries = rom_kirby(rom_path)
            rom = EX.rom_animations(entries, f, 'KIRBY')
        except (OSError, ValueError, KeyError) as e:
            print(f'  note: no Kirby animations exported ({e})')
        taken = {x['name'] for x in own}
        for x in rom:
            while x['name'] in taken:
                x['name'] += '_'
            taken.add(x['name'])
        ex.fighter_anims = own + rom
    ex.manifest['source'] = dict(kind='kirby_hat', folder=os.path.abspath(os.path.dirname(hat_bin)),
                                 rom=os.path.abspath(rom_path))
    ex.manifest['kirby_hat'] = dict(info, joint=HAT_JOINT, item_joint=ITEM_JOINT)
    import gamedata as GD
    ex.manifest['hurtboxes'] = GD.hurtboxes(f, {})
    ex.run()
    if preview:
        import preview as PV
        for p in PV.save_sheets(out_dir):
            print(f'preview: {p}')
    print(f'Kirby with the hat (model part {info.get("hat_variant")} of joint {HAT_JOINT}'
          + (f', item: part {info["item_variant"]} of joint {ITEM_JOINT}' if 'item_variant' in info else '')
          + f') -> {out_dir}')
    return out_dir


ANIM_DIR = 'animations'      # (in the kirby_hat folder) Kirby's animations for the copied moves


def hat_animations(char_dir, f):
    """The character's kirby_hat/animations/*.bin (Kirby's animations for its copied moves), with
    the flags its main.asm gives the Kirby actions playing them (add_new_action_params(KIRBY /
    JKIRBY, ...); -1: Kirby's vanilla ones). Source 'kirby_hat/animations/<file>'."""
    import export_model as EX
    import figatree as FT
    import motions as MO
    adir = os.path.join(char_dir, 'kirby_hat', ANIM_DIR)
    if not os.path.isdir(adir):
        return []
    name = os.path.basename(os.path.normpath(char_dir))
    try:
        mot, _ = MO.folder_motions(char_dir, name, 'KIRBY', anim_dir=f'kirby_hat/{ANIM_DIR}',
                                   file_kind='KIRBY_ANIM', owners={'KIRBY', 'JKIRBY'})
    except OSError as e:
        print(f'  note: Kirby action flags not read ({e}); guessing them from each file\'s joint count')
        mot = {}
    out = []
    for fn in sorted(os.listdir(adir), key=str.lower):
        if not fn.lower().endswith('.bin'):
            continue
        stem = fn[:-4]
        data = open(os.path.join(adir, fn), 'rb').read()
        e = mot.get(stem) or dict(flags=None, actions=[], conflicts=[])
        e['actions'] = list(dict.fromkeys(e['actions']))      # (KIRBY and JKIRBY lines: the same names)
        flags, guessed = FT.pick_flags(f, data, [e['flags']] + [c[1] for c in e['conflicts']])
        try:
            anim = FT.load(f, data, flags=flags)
        except Exception as err:          # (a file that isn't an animation)
            print(f'  note: kirby_hat/{ANIM_DIR}/{fn}: not read ({err})')
            continue
        out.append(dict(name=EX.animation_name([stem] + e['actions'], ' (copy)'), anim=anim,
                        actions=e['actions'], flags=flags, guessed=guessed or not e['actions'],
                        source=f'kirby_hat/{ANIM_DIR}/{fn}'))
    print(f'  {len(out)} kirby_hat animations ({sum(1 for x in out if x["guessed"])} with guessed flags)')
    return out


def import_animations(work_dir, export_dir, man, f, hat_dir):
    """The character's Kirby animations edited in Blender (blend_to_gltf.py's changed fighter
    animations whose source is kirby_hat/animations/<file>) -> HAT_DIR/animations/<file>.
    (Flags changed in Blender: main.asm's add_new_action_params lines are left for you.)"""
    import figatree as FT
    try:
        changed = json.load(open(os.path.join(work_dir, 'costumes.json'))).get('fighter_anims') or []
    except (OSError, ValueError):
        changed = []
    infos = man.get('fighter_animations') or {}
    prefix = f'kirby_hat/{ANIM_DIR}/'
    for c in changed:
        src = (infos.get(c['export_name']) or {}).get('source') or ''
        if not src.startswith(prefix):
            if c.get('export_name') in infos:
                print(f'  note: {c["name"]}: Kirby\'s own (ROM) animation, edited: not written')
            continue
        flags = int(str(c['flags']), 16)
        written = FT.written_flags(flags)
        loops = {b: True for b in c['values']} if c.get('loop') else None
        data = FT.encode_animation(f, written, c['values'], c['frames'], loops=loops, candidates=c.get('keys'))
        adir = os.path.join(hat_dir, ANIM_DIR)
        os.makedirs(adir, exist_ok=True)
        path = os.path.join(adir, src[len(prefix):])
        before = os.path.getsize(path) if os.path.exists(path) else 0
        open(path, 'wb').write(data)
        msg = f'  kirby animation {src[len(prefix):]}: {before} -> {len(data)} bytes'
        if written != int(str(c.get('flags_before') or '0'), 16):
            msg += f'; flags now {written:08X}: update its add_new_action_params lines in main.asm'
        print(msg)


def import_blend(work_dir, export_dir, hat_bin, rom_path=None):
    """blend_to_gltf.py's output of the Kirby blend -> kirby_hat.bin."""
    man = json.load(open(os.path.join(export_dir, 'manifest.json')))
    kh = man['kirby_hat']
    rom_path = rom_path or (man.get('source') or {}).get('rom') or DEFAULT_ROM
    exported = open(os.path.join(export_dir, 'exported_' + BIN), 'rb').read()
    f, _ = kirby_with_hat(rom_path, exported)              # what the .blend's materials refer to
    _, char_raw, char_head, _ = rom_kirby(rom_path)
    n = man.get('costumes') or costumes_of(f)
    textures = IF.Materials(work_dir, n, f.char, os.path.join(export_dir, 'manifest.json'))
    # the hat part's first MObjs are Kirby's body's (identical copies: the export names them like
    # his own body's materials): their triangles are the body's, which the file takes from the ROM
    n_body = len(MF.pointer_run(f.char_int, body_fields(f)['hi'][1]))
    body = {name for p in man.get('model_parts') or [] if p['joint'] == HAT_JOINT and p['variant'] >= kh['hat_variant']
            for name in (p.get('mobj_order') or [])[:n_body] if name}
    objs = {o['name']: o for o in IF.load_gltf(os.path.join(work_dir, 'parts.gltf'))} \
        if os.path.exists(os.path.join(work_dir, 'parts.gltf')) else {}

    body_order = {(p['variant'], p['detail']): (p.get('mobj_order') or [])[:n_body]
                  for p in man.get('model_parts') or [] if p['joint'] == HAT_JOINT}
    # the body's triangles as exported (the ROM body): per hat part, from the export's model.gltf
    exp_gl = os.path.join(export_dir, 'model.gltf')
    exp_objs = {o['name']: o for o in IF.load_gltf(exp_gl)} if os.path.exists(exp_gl) else {}

    def rom_body_tris(name):
        o = exp_objs.get(name)
        return sum(1 for m, _ in o['tris'] if IF.base_mat(m) in body) if o else None

    # parts whose file already drew their own body (exported that way): keep drawing the blend's
    own_body = set()
    for key, details in (kh.get('own_body') or {}).items():
        v = kh['hat_variant'] if key == 'hat' else \
            (kh.get('extra_hat_variants') or [])[int(key.rsplit('_', 1)[1])]
        own_body |= {(v, d) for d in details}

    def part(variant, joint):
        out = {}
        for d in ('hi', 'lo'):
            o = objs.get(f'part{variant}_{d}_j{joint:02d}')
            if o is not None:
                out[d] = dict(o, tris=[(m, t) for m, t in o['tris'] if IF.base_mat(m) not in body])
                own = [(m, t) for m, t in o['tris'] if IF.base_mat(m) in body]
                # Kirby's body edited (some of its triangles now another material, e.g. fur, or
                # reshaped: its triangle count changed): the file draws the blend's body
                ref = rom_body_tris(f'part{variant}_{d}_j{joint:02d}') if joint == HAT_JOINT else None
                if (ref and len(own) != ref) or (joint == HAT_JOINT and (variant, d) in own_body):
                    out[d]['body'] = dict(tris=own, names=body_order.get((variant, d), []))
        return out
    hat = part(kh['hat_variant'], HAT_JOINT)
    # (no item exported: a new part<N>_hi_j13 after Kirby's own joint 13 parts)
    item = part(kh.get('item_variant', len(f.modelparts.get(ITEM_JOINT, []))), ITEM_JOINT)
    if not hat:
        raise SystemExit(f'the Kirby blend has no part{kh.get("hat_variant")}_hi_j02 (the hat): not written')
    # extra hats: the body's parts after the hat's, in order (new ones: more part<V>_hi_j02)
    import re
    extra_variants = sorted({int(m.group(1)) for m in (re.match(r'part(\d+)_(?:hi|lo)_j02$', nm) for nm in objs)
                             if m and int(m.group(1)) > kh['hat_variant']})
    extras = [x for x in (part(v, HAT_JOINT) for v in extra_variants) if x]
    data = pack(f, char_raw, char_head, textures, hat, item, n, extra_hats=extras)
    open(hat_bin, 'wb').write(data)
    print(f'wrote {hat_bin} ({len(data)} bytes)')
    import_animations(work_dir, export_dir, man, f, os.path.dirname(os.path.abspath(hat_bin)))
    return hat_bin


# ---------------------------------------------------------------- other sources
class FlatMaterials(IF.Materials):
    """IF.Materials for new materials from images / colors: {name: (png path or None, linear rgb)}."""

    def __init__(self, table, costumes, base_char, props=None):
        self.props = props or {}
        self.manifest, self.root = {'materials': {}}, None
        self.costumes, self.base = costumes, base_char
        self.anims, self.cloak_safe, self.lights, self.mat = None, True, {}, {}
        self.table = {name: {str(c): ({'image': png} if png else {'color': color}) for c in range(costumes)}
                      for name, (png, color) in table.items()}


def pack_gltf(gltf_path, textures_dir, props, rom_path=DEFAULT_ROM):
    """A kirby_hat_gltf.py glTF (hat_hi / hat_lo / item_hi / item_lo in Kirby's model space;
    materials' images textures_dir/<name>.png) -> kirby_hat.bin bytes."""
    import kirby_hat_gltf as KG
    f, char_raw, char_head, _ = rom_kirby(rom_path)
    meshes = KG.load_meshes(gltf_path)
    gl = json.load(open(gltf_path))
    table = {}
    for m in gl.get('materials', []):
        name = KG.base_name(m.get('name'))
        png = os.path.join(textures_dir, name + '.png')
        table[name] = (png if os.path.exists(png) else None,
                       ((m.get('pbrMetallicRoughness') or {}).get('baseColorFactor') or [0.8] * 3)[:3])
    textures = FlatMaterials(table, 1, f.char, props)

    def obj(name):
        m = meshes[name]
        return dict(m, name=name, joint=np.full(len(m['pos']), KG.joint_of(name)))
    hat = {d: obj(f'hat_{d}') for d in ('hi', 'lo') if f'hat_{d}' in meshes}
    item = {d: obj(f'item_{d}') for d in ('hi', 'lo') if f'item_{d}' in meshes}
    extras = [{d: obj(f'hat{k}_{d}') for d in ('hi', 'lo') if f'hat{k}_{d}' in meshes}
              for k in range(1, 1 + max([int(n[3:].split('_')[0]) for n in meshes if n[3:4].isdigit()] or [0]))]
    return pack(f, char_raw, char_head, textures, hat, item, 1, extra_hats=[x for x in extras if x])


def new_hat(hat_dir, rom_path=DEFAULT_ROM):
    path = os.path.join(hat_dir, BIN)
    if os.path.exists(path):
        raise SystemExit(f'{path} already exists')
    os.makedirs(hat_dir, exist_ok=True)
    f, char_raw, char_head, _ = rom_kirby(rom_path)
    open(path, 'wb').write(pack(f, char_raw, char_head, FlatMaterials({}, 1, f.char), {}, {}, 1))
    return path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rom', default=DEFAULT_ROM, help='Kirby from this ROM (default: Remix\'s original.z64)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('new', help='a kirby_hat.bin with no hat yet').add_argument('hat_dir')
    sub.add_parser('info', help='the offsets the build uses').add_argument('bin')
    p = sub.add_parser('preview', help='render Kirby wearing the hat (and the item)')
    p.add_argument('bin')
    p.add_argument('out_png')
    p.add_argument('--extra', type=int, default=0, help='show extra hat N (1, 2...) instead of the hat')
    g = sub.add_parser('pack-gltf', help='a kirby_hat_gltf.py glTF + textures -> kirby_hat.bin')
    g.add_argument('gltf')
    g.add_argument('textures_dir')
    g.add_argument('out')
    g.add_argument('--props', help='JSON {material: {n64_wrap_s, n64_cull...}}')
    a = ap.parse_args(argv)
    if a.cmd == 'new':
        print('wrote', new_hat(a.hat_dir, a.rom))
    elif a.cmd == 'info':
        off, head = asm_offsets(open(a.bin, 'rb').read())
        fmt = lambda d: ', '.join(f'{k} 0x{v:X}' if v >= 0 else f'{k} -' for k, v in d.items() if k != 'extras')  # noqa: E731
        print(f'chain head 0x{head:X}, ' + fmt(off))
        for k, x in enumerate(off['extras']):
            print(f'extra hat {k + 1}: ' + fmt(x))
    elif a.cmd == 'pack-gltf':
        data = pack_gltf(a.gltf, a.textures_dir, json.loads(a.props) if a.props else {}, a.rom)
        open(a.out, 'wb').write(data)
        print(f'wrote {a.out} ({len(data)} bytes)')
    else:
        import preview as PV
        tmp = tempfile.mkdtemp(prefix='kirby_hat_')
        try:
            export(a.bin, tmp, a.rom, animations=False, preview=False)
            man = json.load(open(os.path.join(tmp, 'manifest.json')))['kirby_hat']
            m = PV.Model(tmp)
            rows = []
            for c in range(json.load(open(os.path.join(tmp, 'manifest.json')))['costumes']):
                for d in ('hi', 'lo'):
                    v = man['extra_hat_variants'][a.extra - 1] if a.extra else man['hat_variant']
                    tris = m.tris(f'part{v}_{d}_j{HAT_JOINT:02d}', c)
                    if 'item_variant' in man:
                        tris += m.tris(f'part{man["item_variant"]}_{d}_j{ITEM_JOINT:02d}', c)
                    rows.append(PV.view_row(tris, 200, f'costume {c} {d}'))
            PV.vstack(rows).save(a.out_png)
            print('preview:', a.out_png)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
