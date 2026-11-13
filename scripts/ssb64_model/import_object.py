#!/usr/bin/env python3
"""Rebuild an item / projectile model file from an edited .blend (export_model.py --object).

    # (ssb64_model.py import OUT_DIR does both steps)
    # 1. inside Blender: export the .blend's geometry + material table
    blender -b model.blend --python blend_to_gltf.py -- WORK_DIR
    # 2. write the model file (and re-point its attributes file)
    python3 import_object.py FOLDER NAME WORK_DIR [--out OUT_DIR]

FOLDER is the character or stage folder whose config.yaml lists the object's files:
NAME_hitbox (the ITAttributes / WPAttributes) + NAME_gfx (append_files) or NAME (stage
items). The model file is written from scratch: only the new model, plus what the
attributes reach that isn't the model (e.g. anim_joints), so leftover data from the
original export is gone. The attributes file's pointers into it are updated, and the
model file's chain head in config.yaml (it moves with the new layout).
"""
import argparse
import json
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # appender root
import model_files as MF  # noqa: E402
import materials as MAT  # noqa: E402
import matanim as MA  # noqa: E402
import jointanim as JA  # noqa: E402
import gbi_writer as GW  # noqa: E402
import import_fighter as IF  # noqa: E402
import export_model as EX  # noqa: E402
from smashremix_extra import ge_bin as ge  # noqa: E402

DATA, MOBJSUBS, ANIM, MATANIM = 0x0, 0x4, 0x8, 0xC          # attributes fields
MODEL_FIELDS = (DATA, MOBJSUBS, MATANIM)                    # rebuilt from the .blend


def standalone(blob):
    """Blob built at offset 0 -> (file bytes, chain head) with its pointers chained in order."""
    d = bytearray(blob.b)
    fields = sorted(blob.ptrs)
    for k, (fld, tgt) in enumerate(fields):
        nxt = fields[k + 1][0] // 4 if k + 1 < len(fields) else GW.NULL
        struct.pack_into('>HH', d, fld, nxt, tgt // 4)
    return bytes(d), (fields[0][0] if fields else 0x3FFFC)


def set_config_head(cfg_path, name, head):
    """config.yaml: the model file's entry ([name, "HEAD", ...]) gets the new chain head;
    a GoldEditor trim option is dropped (the file has no leftovers to trim)."""
    text = open(cfg_path, encoding='utf-8').read()
    pat = re.compile(r'(\[\s*"?' + re.escape(name) + r'"?\s*,\s*")([0-9A-Fa-f]+)(")')
    if not pat.search(text):
        raise SystemExit(f'{cfg_path}: no [{name}, "HEAD", ...] entry to update')
    text = pat.sub(lambda m: m.group(1) + f'{head:04X}' + m.group(3), text, count=1)
    line_pat = re.compile(r'^(.*\[\s*"?' + re.escape(name) + r'"?\s*,.*)$', re.M)
    m = line_pat.search(text)
    if m and 'trim' in m.group(1):
        line = re.sub(r',\s*trim:\s*(true|yes|1)', '', m.group(1))
        line = re.sub(r'trim:\s*(true|yes|1)\s*,\s*', '', line)
        text = text[:m.start()] + line + text[m.end():]
    open(cfg_path, 'w', encoding='utf-8').write(text)


# what a display list may set after its last triangle to hand the next model a clean state
EPILOGUE_OPS = {0xE7, 0xE2, 0xE3, 0xFC, 0xFA, 0xFB, 0xF9, 0xD9, 0xD7}


def epilogue_of(f, dl):
    """The original DL's state commands after its last triangle (render mode, alpha compare,
    TLUT mode... back to normal). Fighters don't need them (the game resets the render mode
    per joint), but items drawn with no render mode of their own (XLU item display) and
    whatever the game draws next inherit what a model leaves set."""
    d, o, last, cmds = f.char, dl, None, []
    while o + 8 <= len(d):
        op = d[o]
        if op == 0xDF:
            break
        cmds.append(o)
        if op in (0x01, 0x05, 0x06, 0xDE):
            last = len(cmds)
        o += 8
    tail = cmds[last:] if last is not None else []
    return b''.join(d[x:x + 8] for x in tail if d[x] in EPILOGUE_OPS)


def prologue_for(f, d, lights):
    """The model's DL setup (sync, lights, geometry mode...) from its first display list."""
    first = next((MF.linked_display_lists(f.char, f.char_int, j.dl)[0] for j in d['joints'] if j.dl is not None), None)
    return IF.set_lights(IF.prologue_of(f, first) if first is not None else b'', lights)


def material_scripts(f):
    """{MObjSub offset: its material script offset}: stage / item material animations
    (texture scroll, frame cycling...), kept as they are (fighters' are costume scripts,
    rebuilt from the costume colors instead)."""
    out = {}
    for d in f.details.values():
        if d.get('matanim') is None:
            continue
        for j, lst in d['mobjs'].items():
            an = f.char_int.get(d['matanim'] + 4 * j)
            for i, ms in enumerate(lst):
                sc = f.char_int.get(an + 4 * i) if an is not None else None
                if sc is not None:
                    out.setdefault(ms, sc)
    return out


class ModelWriter:
    """Builds models (a DObj tree or a bare display list + MObj lists + material scripts) from
    blend_to_gltf.py meshes into a GW.Blob; materials / images shared between models (stage
    layers) are stored once."""

    def __init__(self, f, textures, blob, kept_scripts=None):
        """kept_scripts: {original MObjSub: its material script, already in the new file}."""
        self.f, self.textures, self.blob = f, textures, blob
        self.kept_scripts = kept_scripts or {}
        self.template_ms = next((ms for d in f.details.values() for lst in d['mobjs'].values() for ms in lst
                                 if MF.u16(f.char, ms + 0x30) & 0x0005 == 0x0005), None)
        self.mobj_of, self.script_of, self.data_at, self.mobj_at = {}, {}, {}, {}

    def store(self, data, align=8):
        if data not in self.data_at:
            self.data_at[data] = self.blob.add(data, align)
        return self.data_at[data]

    def ptr_list(self, targets):
        lst = self.blob.add(b'\0' * (4 * len(targets)), 4)
        for i, t in enumerate(targets):
            self.blob.ptr(lst + 4 * i, t)
        return lst

    def mobjsub(self, name):
        """(as import_fighter.py: an MObjSub copy, its image / palette lists, its script)"""
        if name in self.mobj_of:
            return self.mobj_of[name]
        f, t = self.f, self.textures.get(name)
        imgs = tuple(self.store(x) for x in t.images) if t.image_source == 'list' else ()
        pals = tuple(self.store(p) for p in t.palettes) if t.palette_source == 'list' else ()
        # a new material: a copy of one of the model's MObjs, or (animated, or none to copy) a
        # clean one: no offset, scale 1, white colors
        src = t.mobj if t.mobj is not None else (self.template_ms if not t.animated else None)
        ms = bytearray(f.char[src:src + IF.MOBJSUB_SIZE]) if src is not None else self.clean_mobjsub(t)
        struct.pack_into('>I', ms, 0x04, 0)
        struct.pack_into('>I', ms, 0x2C, 0)
        struct.pack_into('>H', ms, 0x30, t.mobj_flags())
        for k, v in ((t.costume_colors or [{}])[0]).items():
            struct.pack_into('>I', ms, MAT.COLOR_FIELDS[k], int(v, 16))
        if (t.new or src is None) and not t.untextured:
            # (the MObj's image SETTIMG feeds the DL's LOADBLOCK: 16b unless 32b; its TLUT load
            # (flags 0x05) reads 16 colors then, 256 only for an 8b CI image loaded inline)
            struct.pack_into('>BB', ms, 0x02, t.fmt, 3 if t.siz == 3 else (1 if t.siz == 1 and not imgs else 2))
            struct.pack_into('>BB', ms, 0x32, t.fmt, t.siz)
        anim = (self.textures.anims or {}).get(name) if self.textures.anims is not None else None
        script = self.anim_script(name, t, ms, anim) if anim is not None else None
        key = (bytes(ms), imgs, pals)
        if key not in self.mobj_at:
            o = self.blob.add(bytes(ms), 8)
            if imgs:
                self.blob.ptr(o + 0x04, self.ptr_list(imgs))
            if pals:
                self.blob.ptr(o + 0x2C, self.ptr_list(pals))
            self.mobj_at[key] = o
        self.mobj_of[name] = self.mobj_at[key]
        if anim is not None:                            # (Blender's material animation)
            self.script_of[name] = script
        elif t.mobj in self.kept_scripts:               # its original material animation
            self.script_of[name] = self.kept_scripts[t.mobj]
        else:
            sc = t.script(1)
            self.script_of[name] = self.store(sc, 4) if sc else None
        return self.mobj_of[name]

    @staticmethod
    def clean_mobjsub(t):
        ms = bytearray(IF.MOBJSUB_SIZE)
        tw, th = int(getattr(t, 'tile_w', 0) or t.w or 32), int(getattr(t, 'tile_h', 0) or t.h or 32)
        struct.pack_into('>BB', ms, 0x02, t.fmt, t.siz)
        struct.pack_into('>HHHH', ms, 0x08, 32, 0, tw, th)          # (unk08 32: texture scale 1)
        struct.pack_into('>ffffff', ms, 0x14, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0)
        struct.pack_into('>BBHHHH', ms, 0x32, t.fmt, t.siz, 0, 0, tw, th)
        for off in (0x50, 0x58, 0x60):                                # prim, env, light: white
            struct.pack_into('>I', ms, off, 0xFFFFFFFF)
        struct.pack_into('>I', ms, 0x64, 0x808080FF)
        return ms

    def anim_script(self, name, t, ms, anim):
        """Material animation from Blender (blend_to_gltf.py anim_info): the MObjSub gets the
        starting values and the flags its tracks need (ms, edited in place); -> the script's
        offset: the original's when its keyframes weren't changed, a new one otherwise, None
        without animation."""
        entry = (self.textures.manifest.get('materials') or {}).get(name) or {}
        orig_static = entry.get('mobj_static') or {}
        static, tracks = dict(anim.get('static') or {}), dict(anim.get('tracks') or {})
        samples = dict(anim.get('samples') or {})
        frames, loop, loop_start = int(anim.get('frames') or 0), bool(anim.get('loop')), int(anim.get('loop_start') or 0)
        flags = struct.unpack_from('>H', ms, 0x30)[0]
        old_flags = flags
        for track, v in static.items():                 # starting values (the MObjSub's fields)
            if track in MA.FIELD:
                if t.mobj is None or abs(v - orig_static.get(track, v + 1)) > 1e-6:
                    struct.pack_into('>f', ms, MA.FIELD[track], v)
            elif track == 'lfrac':
                ms[0x54] = max(0, min(255, round(v * 255)))
            elif track in MA.COLOR_FIELD:
                struct.pack_into('>I', ms, MA.COLOR_FIELD[track], int(v))
            elif v:                                     # an index: the MObj starts at 0, a script sets it
                tracks[track] = [[0, v, 'CONSTANT', 0.0, 0.0]]
                samples[track] = [v] * (frames + 1)
            flags |= MA.FLAG_OF[track] if track not in ('tex', 'pal') else 0
        for track in tracks:
            flags |= MA.FLAG_OF[track] if track not in ('tex', 'pal') else 0
        for bit, size_at in ((0x20, 0x0C), (0x40, 0x38)):
            if flags & bit and not old_flags & bit:     # the MObj now sets the tile: its size
                if bit == 0x20:
                    struct.pack_into('>H', ms, 0x0A, 0)
                    struct.pack_into('>i', ms, 0x10, 0)
                    for k, v in (('trau', 0.0), ('trav', 0.0), ('scau', 1.0), ('scav', 1.0)):
                        if k not in static and k not in tracks:
                            struct.pack_into('>f', ms, MA.FIELD[k], v)
                struct.pack_into('>HH', ms, size_at, int(getattr(t, 'tile_w', 0) or t.w or 32),
                                 int(getattr(t, 'tile_h', 0) or t.h or 32))
        struct.pack_into('>H', ms, 0x30, flags)
        if not tracks:
            if entry.get('anim'):
                print(f'  {name}: material animation removed')
            return None
        man = entry.get('anim')
        if man and t.mobj in self.kept_scripts and self._unchanged(man, tracks, samples, frames, loop, loop_start):
            return self.kept_scripts[t.mobj]
        if frames < 1:
            raise SystemExit(f'{name}: material animation of {frames} frames (set its Length)')
        script, ptrs, bad = MA.build(tracks, samples, frames, loop, loop_start)
        key = (script, tuple(ptrs))
        if key not in self.data_at:
            o = self.blob.add(script, 4)
            for fld, tgt in ptrs:
                self.blob.ptr(o + fld, o + tgt)
            self.data_at[key] = o
        print(f'  {name}: material animation written ({", ".join(MA.PROP[k] for k in tracks)}; {frames} frames'
              + (', loops' if loop else '') + ')'
              + (f'; written frame by frame: {", ".join(MA.PROP[k] for k in bad)}' if bad else ''))
        return self.data_at[key]

    @staticmethod
    def _unchanged(man, tracks, samples, frames, loop, loop_start):
        """Blender's animation = the exported one (same values every frame, same loop)?"""
        if (int(man['frames']), bool(man['loop']), int(man.get('loop_start', 0))) != (frames, loop, loop_start):
            return False
        if set(man['keys']) != set(tracks):
            return False
        for k, keys in man['keys'].items():
            seq = samples.get(k) or []
            if len(seq) != frames + 1:
                return False
            for n in range(frames + 1):
                if not MA._close(MA.eval_keys(keys, n, k), seq[n], k):
                    return False
        return True

    def build(self, detail, objs, need=()):
        """f.details[detail]'s model from objs -> ({DATA / MOBJSUBS / MATANIM: new target}, dls):
        same DObjDesc array (transforms, ids: code / collision groups address them by index),
        DObjDLLink arrays where the original had them, one DL per link list. need: roles
        (MOBJSUBS / MATANIM) the original had: written as empty per-joint arrays if unused."""
        f, blob, textures = self.f, self.blob, self.textures
        d = f.details[detail]
        joints = d['joints']
        W = [j.world for j in joints]
        links = {j.index for j in joints if j.dl is not None and MF.dl_links(f.char, f.char_int, j.dl)}
        prologue = prologue_for(f, d, textures.lights)
        order = {int(j): v for j, v in (textures.manifest.get('mobj_order') or {}).get(detail, {}).items()}
        # one display list per DObjDLLink list (materials drawn from list 1 = translucent... keep it)
        man_mats = textures.manifest.get('materials') or {}
        link_of = lambda m: int((man_mats.get(IF.base_mat(m)) or {}).get('dl_link', 0))  # noqa: E731
        # each list's DLs end like the original's DLs of that list (render mode restored...)
        epi = {}
        for j in joints:
            if j.dl is None:
                continue
            for lid, dl in MF.dl_links(f.char, f.char_int, j.dl) or [(0, j.dl)]:
                e = epilogue_of(f, dl)
                if e and lid not in epi:
                    epi[lid] = e
        by_link = {}
        for lid in sorted({link_of(m) for o in objs for m, _ in o['tris']}):
            sub = [dict(o, tris=[(m, t) for m, t in o['tris'] if link_of(m) == lid]) for o in objs]
            sub = [o for o in sub if o['tris']]
            by_link[lid] = IF.build_joints(sub, W, textures, blob, prologue, self.store, mobj_order=order,
                                           epilogue=epi.get(lid, epi.get(0, b'')), keep_order=True,
                                           draw_order={k: v.get('draw_order') or [] for k, v in
                                                       (textures.manifest.get('objects') or {}).items()},
                                           preserve_order=f.object.get('kind') == 'stage',
                                           draw_runs={k: v.get('draw_runs') or [] for k, v in
                                                      (textures.manifest.get('objects') or {}).items()})
        dls, mats = {}, {}                                    # joint -> {list id: dl}; MObj names
        for lid, (ldls, lmats) in by_link.items():
            for j, dl in ldls.items():
                dls.setdefault(j, {})[lid] = dl
            for j, names in lmats.items():
                have = mats.setdefault(j, list(names))
                if have[:len(names)] != names[:len(have)]:
                    raise SystemExit(f'{detail} joint {j}: its materials in DObjDLLink list {lid} use MObjs at '
                                     f'other indices than list 0 ({names} / {have})')
                if len(names) > len(have):
                    mats[j] = list(names)
        targets = {}
        if any(mats.values()):                                # MObj lists (+ scripts), per joint
            p_ms = blob.add(b'\0' * (4 * len(joints)), 4)
            p_an = blob.add(b'\0' * (4 * len(joints)), 4)
            scripts = False
            for j, names in mats.items():
                lst = blob.add(b'\0' * (4 * (len(names) + 1)), 4)
                an = blob.add(b'\0' * (4 * max(len(names), 1)), 4)
                for i, n in enumerate(names):
                    blob.ptr(lst + 4 * i, self.mobjsub(n))
                    if self.script_of[n] is not None:
                        blob.ptr(an + 4 * i, self.script_of[n])
                        scripts = True
                blob.ptr(p_ms + 4 * j, lst)
                blob.ptr(p_an + 4 * j, an)
            targets[MOBJSUBS] = p_ms
            if scripts:
                targets[MATANIM] = p_an
        for role in need:                                     # (NULL per joint: no materials)
            if role in (MOBJSUBS, MATANIM) and role not in targets:
                targets[role] = blob.add(b'\0' * (4 * len(joints)), 4)

        def joint_dl(j):
            per = dls[j]
            if j not in links and set(per) == {0}:
                return per[0]
            ids = sorted(per)                                # DObjDLLink {id, dl} ..., end (id 4)
            lk = blob.add(b''.join(struct.pack('>II', lid, 0) for lid in ids) + struct.pack('>II', 4, 0), 8)
            for k, lid in enumerate(ids):
                blob.ptr(lk + 8 * k + 4, per[lid])
            return lk
        if d.get('dobjdesc') is None:                         # a bare display list
            if 0 not in dls:
                raise SystemExit(f'{detail}: the model is empty')
            targets[DATA] = joint_dl(0)
        else:
            n = len(joints)
            arr = bytearray(f.char[d['dobjdesc']:d['dobjdesc'] + MF.DOBJDESC_SIZE * n + 4])
            for j in range(n):
                struct.pack_into('>I', arr, MF.DOBJDESC_SIZE * j + 4, 0)
            do = blob.add(bytes(arr), 8)
            for j in dls:
                blob.ptr(do + MF.DOBJDESC_SIZE * j + 4, joint_dl(j))
            targets[DATA] = do
        return targets, dls


def write_joint_anims(f, work_dir, blob, remap):
    """Each model's (stage layer's, item's) joint animation scripts from Blender's joint transforms: a joint's
    original script where it's unchanged (same values every frame, same length / loop), a new
    one where it was edited or newly animated, none where its animation was removed. ->
    {anim_joints field (the model's field + 4): new array} for the models that changed
    (others keep theirs)."""
    tab = json.load(open(os.path.join(work_dir, 'costumes.json')))
    blend = tab.get('joint_anims')
    if blend is None:                                     # (an older blend_to_gltf.py: kept)
        return {}
    out = {}
    for name, d in f.details.items():
        joints, arr = d['joints'], d.get('anim')
        orig = {j.index: f.char_int.get(arr + 4 * j.index) for j in joints} if arr is not None else {}
        orig = {k: v for k, v in orig.items() if v is not None}
        mine = blend.get(name) or {}
        ptrs, changed = {}, False
        for j in joints:
            b, sc = mine.get(str(j.index)), orig.get(j.index)
            if b is None:
                if sc is not None:
                    print(f'  {name} joint {j.index}: animation removed')
                    changed = True
                continue
            prev, samples = None, {k: [] for k in JA.EDITABLE}
            for m in b['matrices']:
                prev = JA.decompose(np.array(m, float).reshape(4, 4), prev)
                for k in JA.EDITABLE:
                    samples[k].append(prev[k])
            new = dict(frames=int(b['frames']), loop=bool(b['loop']), loop_start=int(b.get('loop_start', 0)),
                       samples=samples)
            if new['frames'] < 1 and sc is None:
                continue
            old = JA.play(f.char, f.char_int, sc, j) if sc is not None else None
            if sc is not None and (JA.unchanged(old, new) or JA.uses_interp(f.char, f.char_int, sc)):
                if not JA.unchanged(old, new):
                    print(f'  warning: {name} joint {j.index}: moves along a path (TraI), which can\'t be '
                          'edited: kept as it was')
                ptrs[j.index] = remap[sc]
                continue
            try:
                script, fixups, bad = JA.build(new, JA.rest_values(j), keep_tracks=old['tracks'] if old else ())
            except ValueError as err:
                raise SystemExit(f'{name} joint {j.index}: {err}')
            changed = True
            if script is None:
                print(f'  {name} joint {j.index}: stays at its rest pose: no animation')
                continue
            o = blob.add(script, 4)
            for fld, tgt in fixups:
                blob.ptr(o + fld, o + tgt)
            ptrs[j.index] = o
            print(f'  {name} joint {j.index}: animation written ({new["frames"]} frames'
                  + (', loops' if new['loop'] else '') + ')'
                  + (f'; frame by frame: {", ".join(bad)}' if bad else ''))
        if changed:
            lst = blob.add(b'\0' * (4 * len(joints)), 4)
            for ji, tgt in ptrs.items():
                blob.ptr(lst + 4 * ji, tgt)
            out[d['field'] + 4] = lst
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('folder', help='character or stage folder (its config.yaml lists the files)')
    ap.add_argument('name', help='the object: NAME_hitbox + NAME_gfx / NAME')
    ap.add_argument('work_dir', help='blend_to_gltf.py output')
    ap.add_argument('--out', help='write the files here (default: the folder, updating config.yaml)')
    ap.add_argument('--manifest', help="the export's manifest.json (default: the one the .blend was built from)")
    a = ap.parse_args(argv)

    cfg_path = os.path.join(a.folder, 'config.yaml')
    cfg = EX.read_config(a.folder)
    kw = EX.object_files(cfg, a.folder, a.name)
    if 'model_path' not in kw:
        raise SystemExit(f'{a.name}: no model file in config.yaml (single-file objects are not supported yet)')
    f = MF.load_object(**kw)
    attr_off, ext_head = kw.get('attr_offset', 0), kw['attr_ext_head']
    model_name = os.path.splitext(os.path.basename(kw['model_path']))[0]
    hb = bytearray(f.main)
    ext = MF.chain(bytes(hb), ext_head)                     # {field: target}, in chain order
    ext_fields = list(ext)

    reqlist_path = os.path.splitext(kw['attr_path'])[0] + '_reqlist.txt'
    req_raw = open(reqlist_path, 'rb').read()
    newline = '\r\n' if b'\r\n' in req_raw else '\n'
    req = req_raw.decode('utf-8').splitlines()
    end = next((i for i, ln in enumerate(req) if ln.startswith('END OF')), len(req))
    if end != len(ext_fields):
        raise SystemExit(f'{reqlist_path}: {end} lines but {len(ext_fields)} external pointers')
    token = req[ext_fields.index(attr_off + DATA)].strip()       # the model file's ${...}
    into_model = [fld for fld, ln in zip(ext_fields, req) if ln.strip() == token]

    textures = IF.Materials(a.work_dir, 1, f.char, a.manifest)
    textures.cloak_safe = False                      # items keep their own render modes

    # what the attributes reach in the model file besides the model: kept (anim_joints, ...)
    keep = [ext[fld] for fld in into_model if fld - attr_off not in MODEL_FIELDS]
    scripts = material_scripts(f)                  # material animations: kept as they are
    if keep or scripts:
        res = ge.gc(f.char, keep + sorted(set(scripts.values())), head=kw['model_int_head'])
        base, base_head, remap = bytearray(res.data), res.new_head, res.remap
    else:
        base, base_head, remap = bytearray(), None, {}
    base += b'\0' * (-len(base) % 8)
    blob = GW.Blob(len(base))

    path = os.path.join(a.work_dir, 'body.gltf')
    if not os.path.exists(path):
        raise SystemExit(f'{path}: not found (run blend_to_gltf.py on the .blend first)')
    objs = IF.load_gltf(path)
    writer = ModelWriter(f, textures, blob, {ms: remap[sc] for ms, sc in scripts.items()})
    targets, dls = writer.build('hi', objs, [r for r in (MOBJSUBS, MATANIM) if attr_off + r in ext])
    # joint animations (anim_joints, attributes +8): Blender's (blend_to_gltf.py joint_anims)
    for fld, tgt in write_joint_anims(f, a.work_dir, blob, remap).items():
        targets[fld - attr_off] = tgt

    if base_head is None:
        new, head = standalone(blob)
    else:
        new = bytes(GW.append_blob(base, blob, base_head, lambda d, h: MF.chain(d, h)))
        head = base_head
    if len(new) > 0x3FFFC:
        raise SystemExit(f'{model_name}.bin is {len(new)} bytes: over the 0x3FFFC limit')

    # ---- re-point the attributes (kept fields: remapped; model fields: the new data)
    for fld in into_model:
        rel = fld - attr_off
        if rel in MODEL_FIELDS or rel in targets:
            if rel not in targets:
                raise SystemExit(f'attributes +0x{rel:X} pointed into the model but the new model has no such data')
            tgt = targets[rel]
        else:
            tgt = remap[ext[fld]]
        struct.pack_into('>H', hb, fld + 2, tgt // 4)
    added = 0
    for rel, tgt in targets.items():
        fld = attr_off + rel
        if fld in ext:
            continue
        if MF.u32(hb, fld) != 0:
            raise SystemExit(f'attributes +0x{rel:X} holds data, not an (empty) pointer field')
        tail = list(MF.chain(bytes(hb), ext_head))[-1]
        struct.pack_into('>H', hb, tail, fld // 4)
        struct.pack_into('>HH', hb, fld, GW.NULL, tgt // 4)
        req.insert(len(MF.chain(bytes(hb), ext_head)) - 1, token)
        added += 1

    out = a.out or a.folder
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, model_name + '.bin'), 'wb').write(new)
    open(os.path.join(out, os.path.basename(kw['attr_path'])), 'wb').write(hb)
    if added:
        open(os.path.join(out, os.path.basename(reqlist_path)), 'w', encoding='utf-8', newline='').write(
            newline.join(req) + (newline if req_raw.endswith(b'\n') else ''))
    if a.out is None:
        set_config_head(cfg_path, model_name, head)
    tris = sum(len(o['tris']) for o in objs)
    print(f'{model_name}.bin: {len(f.char)} -> {len(new)} bytes, chain head 0x{head:X}; '
          f'{tris} triangles on joints {sorted(dls)}'
          + (f'; {added} new attribute pointers' if added else ''))
    if a.out:
        print(f'  (config.yaml not updated: set {model_name}\'s chain head to "{head:04X}")')


if __name__ == '__main__':
    main()
