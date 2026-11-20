#!/usr/bin/env python3
"""Build a fighter's main.bin + character.bin from an edited model.blend.

    # (ssb64_model.py import OUT_DIR does both steps)
    # 1. inside Blender: export the .blend's geometry + costume material table
    blender -b model.blend --python blend_to_gltf.py -- WORK_DIR
    # 2. compile into the character's .bin files
    python3 import_fighter.py extra_characters/<Name> WORK_DIR [--out OUT_DIR] [--manifest EXPORT_DIR/manifest.json]

The base main.bin / character.bin (in the character folder) provide everything
that isn't the model (skeleton, hurtboxes, attributes, other data); the model
(hi/lo body, materials, costume scripts, alternate parts) is rebuilt from the
.blend. Unreachable data is then removed from character.bin (it doesn't grow
with every build) and main.bin's pointers into it are updated.

Rules for the .blend: vertices belong to one joint bone each (joint_NN);
a triangle may join two joints (knees, elbows, neck): it's drawn with the
earlier joint's cached vertices, like the game's own models. Textures are
CI4 (16 colors per costume image; more get reduced), power-of-two sized.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # appender root
import model_files as MF  # noqa: E402
import materials as MAT  # noqa: E402
import gbi_writer as GW  # noqa: E402
from smashremix_extra import ge_bin as ge  # noqa: E402

MOBJSUB_SIZE = 0x78
MIN_FREE_SLOTS = 9


# ---------------------------------------------------------------- glTF
def load_gltf(path):
    """[{name, pos, nrm, uv, joint, tris: [(material, (i, j, k))]}] with joint = bone
    index from the 'joint_NN' node names."""
    gl = json.load(open(path))
    bufs = [open(os.path.join(os.path.dirname(path), b['uri']), 'rb').read() for b in gl['buffers']]
    ct = {5126: 'f4', 5123: 'u2', 5125: 'u4', 5121: 'u1'}
    nc = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}

    def acc(i):
        a = gl['accessors'][i]
        bv = gl['bufferViews'][a['bufferView']]
        dt = np.dtype('<' + ct[a['componentType']])
        n = nc[a['type']]
        start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        stride = bv.get('byteStride', 0) or dt.itemsize * n
        raw = bufs[bv['buffer']]
        out = np.array([np.frombuffer(raw, dt, n, start + k * stride) for k in range(a['count'])])
        if a.get('normalized'):
            out = out / np.iinfo(dt).max
        return out.reshape(a['count'], n) if n > 1 else out.reshape(-1)
    mats = [m.get('name', f'm{i}') for i, m in enumerate(gl.get('materials', []))]
    objs = []
    for nd in gl['nodes']:
        if 'mesh' not in nd:
            continue
        if 'skin' not in nd:
            raise SystemExit(f"{nd.get('name')}: not skinned to the armature")
        jn = gl['skins'][nd['skin']]['joints']
        bone = []
        for j in jn:
            m = re.search(r'joint_(\d+)$', gl['nodes'][j].get('name', ''))     # (stages: L<n>_joint_NN)
            bone.append(int(m.group(1)) if m else None)
        pos, nrm, uv, jnt, col, tris = [], [], [], [], [], []
        for prim in gl['meshes'][nd['mesh']]['primitives']:
            at = prim['attributes']
            base = sum(len(p) for p in pos)
            P = acc(at['POSITION'])
            pos.append(P)
            nrm.append(acc(at['NORMAL']) if 'NORMAL' in at else np.zeros_like(P))
            uv.append(acc(at['TEXCOORD_0']) if 'TEXCOORD_0' in at else np.zeros((len(P), 2)))
            # vertex colors as the game's (sRGB) values: from the N64_RG / N64_BA UV maps
            # blend_to_gltf.py adds (exact), else COLOR_0 (linear)
            uv_names = [k for k in sorted(at) if k.startswith('TEXCOORD_') and k != 'TEXCOORD_0']
            if len(uv_names) >= 2:
                rg, ba = acc(at[uv_names[-2]]), acc(at[uv_names[-1]])
                c = np.stack([rg[:, 0], 1 - rg[:, 1], ba[:, 0], 1 - ba[:, 1]], 1)   # glTF flips V
            elif 'COLOR_0' in at:
                c = acc(at['COLOR_0'])
                if c.shape[1] == 3:
                    c = np.hstack([c, np.ones((len(c), 1))])
                c[:, :3] = [[MF.linear_to_srgb(x) for x in row] for row in c[:, :3]]
            else:
                c = np.ones((len(P), 4))
            col.append(c)
            J, W = acc(at['JOINTS_0']), acc(at['WEIGHTS_0'])
            jj = [bone[int(J[k][np.argmax(W[k])])] for k in range(len(J))]
            if None in jj:
                raise SystemExit(f"{nd.get('name')}: vertices on a bone that isn't joint_NN")
            jnt.append(np.array(jj))
            idx = acc(prim['indices']).astype(int).reshape(-1, 3) if 'indices' in prim else \
                np.arange(len(P)).reshape(-1, 3)
            mat = mats[prim['material']] if 'material' in prim else None
            tris += [(mat, t + base) for t in idx]
        objs.append(dict(name=re.sub(r'\.\d+$', '', nd.get('name', '')), pos=np.vstack(pos).astype(float),
                         nrm=np.vstack(nrm).astype(float), uv=np.vstack(uv).astype(float),
                         col=np.vstack(col).astype(float),
                         joint=np.concatenate(jnt), tris=tris))
    return objs


def GD_set_yaml(text, key, lines):
    import gamedata as GD
    return GD.set_yaml(text, key, lines)


def base_mat(name):
    return re.sub(r'(_c\d+)?(\.\d+)?$', '', name or 'flat')


# ---------------------------------------------------------------- materials
class Materials:
    """Material name -> materials.Material, from the export manifest (original
    lists, formats, render state) + Blender's costume table (costumes.json)."""

    def __init__(self, work, costumes, base_char, manifest_path=None):
        tab = json.load(open(os.path.join(work, 'costumes.json')))
        if 'materials' in tab and 'export_dir' in tab:
            self.table, self.props, root = tab['materials'], tab.get('props', {}), tab['export_dir']
            self.lights = tab.get('lights') or {}
            # costumes added in Blender: the costume each copies ({new: source})
            self.sources = {int(k): int(v) for k, v in (tab.get('costume_sources') or {}).items()}
            self.anims = tab.get('anims')        # material animations (None: older blend_to_gltf.py)
        else:                                                           # older costumes.json: table only
            self.table, self.props, root = tab, {}, None
        mp = manifest_path or (os.path.join(root, 'manifest.json') if root else None)
        if mp and os.path.exists(mp):
            self.manifest = json.load(open(mp))
            self.root = os.path.dirname(os.path.abspath(mp))
            want = self.manifest.get('character_sha1')
            if want and want != hashlib.sha1(bytes(base_char)).hexdigest():
                raise SystemExit("the base character.bin isn't the one the .blend was exported from "
                                 '(its original textures are read from it): pass --char with that file')
        else:
            print('  warning: no export manifest found: every material is rebuilt as a new CI4 material')
            self.manifest, self.root = {'materials': {}}, None
        self.costumes, self.base = costumes, base_char
        # textures exported upside down to edit (export_model.TEX_FLIP_V): PNGs and V flipped back
        self.flip_v = bool(self.manifest.get('tex_flip_v'))
        MAT.FLIP_V = self.flip_v
        self.anims = getattr(self, 'anims', None)
        self.cloak_safe = True
        self.lights = getattr(self, 'lights', {})
        self.mat = {}

    def image(self, info, textured):
        if info is None:
            return None
        if info.get('image') and os.path.exists(info['image']):
            return MAT.load_indexed(info['image'])
        if textured:                     # a flat color on an originally textured material: keep the texture
            return None
        c = np.clip(np.array(info.get('color') or [0.8, 0.8, 0.8]), 0, 1)
        srgb = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
        img = np.zeros((4, 4, 4), np.uint8)
        img[...] = [*(srgb * 255).round().astype(int), 255]
        return img

    def get(self, name):
        if name in self.mat:
            return self.mat[name]
        entry = self.manifest['materials'].get(name)
        if entry and entry.get('kind') == 'prim':          # one color, no MObj: an untextured material
            entry = dict(entry, untextured=True, costume_colors=[
                {'prim': ''.join(f'{x:02X}' for x in entry['color'])}] * self.costumes)
        elif entry and entry.get('kind') not in ('mobj', 'tex'):
            entry = None
        per = self.table.get(name, {})
        if not per and not entry:
            raise SystemExit(f'material {name}: not in costumes.json')
        first = per.get('0') or (next(iter(per.values())) if per else None)
        textured = bool(entry) and not entry.get('untextured')    # untextured: Blender's colors count
        sources = getattr(self, 'sources', {}) or {}

        def costume_info(c):
            if str(c) in per:
                return per[str(c)]
            if c in sources and str(sources[c]) in per:     # an added costume: the one it copies
                return per[str(sources[c])]
            # a costume Blender has no material for (low detail, model parts): the file the export
            # gave that costume (the manifest's)
            cos_ = (entry or {}).get('costumes') or []
            c_ = c if c < len(cos_) else sources.get(c, c)
            rel = cos_[c_] if c_ < len(cos_) else None
            p_ = os.path.join(self.root, rel) if rel and self.root else None
            return dict(first or {}, image=p_) if p_ and os.path.exists(p_) else first
        infos = [costume_info(c) for c in range(self.costumes)]
        imgs = [self.image(i_, textured) for i_ in infos]
        if not entry and imgs[0] is None:
            raise SystemExit(f'material {name}: no image or color')
        m = MAT.Material(name, entry, self.base, self.root, imgs, self.props.get(name, {}), self.costumes,
                         cloak_safe=self.cloak_safe, layout=int(self.manifest.get('tex_layout') or 1),
                         costume_paths=[(i_ or {}).get('image') for i_ in infos], sources=sources)
        for w in dict.fromkeys(m.warnings):
            print(f'  warning: {name}: {w}')
        self.mat[name] = m
        return m

    def has(self, name):
        return name in self.mat or name in self.manifest['materials'] or name in self.table

    def order(self, names):
        """Drawing order: blending materials last (they leave their render mode set)."""
        return sorted(names, key=lambda n: (self.get(n).special(), n))


# ---------------------------------------------------------------- model
def build_joints(objs, joints_W, textures, blob, prologue, store, only_joint=None, mobj_order=None, slots=None,
                 epilogue=b'', keep_order=False, draw_order=None, preserve_order=False, draw_runs=None):
    """Display lists per joint; triangles joining two joints are drawn in the
    later joint with the earlier joint's cached vertices (reserved cache slots).
    mobj_order: {joint: [material names]}: the original MObj list order (kept: texture
    parts and code address MObjs by index); None = a slot with no material."""
    Winv = [np.linalg.inv(W) for W in joints_W]
    per_joint, wedge, ordered = {}, {}, {}
    # keep_order (items / stages): materials drawn in the order they come in the meshes (the
    # original display-list order: stage backgrounds drawn without depth test are painted
    # in that order); otherwise blending materials last, by name
    appear = {}                                   # joint -> {material: draw position}

    def note(j, o, name):
        d = appear.setdefault(j, {})
        if not d:                                 # the export's order first (Blender re-sorts)
            for mat in (draw_order or {}).get(o['name'], []):
                d.setdefault(base_mat(mat), len(d))
        d.setdefault(name, len(d))

    def ordered_triangles(o):
        triangles = o['tris']
        runs = (draw_runs or {}).get(o['name'])
        if not preserve_order or not runs:
            return triangles
        by_material = {}
        for mat, tri in triangles:
            by_material.setdefault(base_mat(mat), []).append((mat, tri))
        want, have = {}, {k: len(v) for k, v in by_material.items()}
        for run in runs:
            want[base_mat(run['material'])] = want.get(base_mat(run['material']), 0) + int(run['triangles'])
        if want != have:
            # edited in Blender (triangles added / removed): the export's runs no longer say how its
            # triangles interleave: drawn by material, in the export's material order
            print(f"  note: {o['name']}: edited ({sum(want.values())} -> {sum(have.values())} triangles): drawn by "
                  'material in the export\'s order (its original interleaved draw runs no longer apply)')
            return triangles
        out = []
        for run in runs:
            name, count = base_mat(run['material']), int(run['triangles'])
            entries = by_material.get(name, [])
            out.extend(entries[:count])
            del entries[:count]
        return out

    for o in objs:
        for mat, tri in ordered_triangles(o):
            js = sorted({int(o['joint'][v]) for v in tri})
            if only_joint is not None:
                js = [only_joint]
            name = base_mat(mat)
            tex = textures.get(name)
            keys = []
            for v in tri:
                j = only_joint if only_joint is not None else int(o['joint'][v])
                lp = (Winv[j] @ np.append(o['pos'][v], 1))[:3]
                ln = Winv[j][:3, :3] @ o['nrm'][v]
                ln /= np.linalg.norm(ln) or 1
                tv = o['uv'][v][1]
                st = (o['uv'][v][0] * tex.w, (1 - tv if getattr(textures, 'flip_v', False) else tv) * tex.h)
                k = (o['name'], int(v), name)
                # unlit materials (lighting off): the vertex carries a color instead of a normal
                rgba = None if tex.lit else o['col'][v]
                wedge[k] = (j, GW.vertex(lp, ln, st, rgba))
                keys.append(k)
            if len(js) == 1:
                per_joint.setdefault(js[0], {'own': {}, 'cross': {}})['own'].setdefault(name, []).append(keys)
                if preserve_order:
                    ordered.setdefault(js[0], []).append((name, keys, False))
            elif len(js) == 2:
                per_joint.setdefault(js[1], {'own': {}, 'cross': {}})['cross'].setdefault(name, []).append(keys)
                if preserve_order:
                    ordered.setdefault(js[1], []).append((name, keys, True))
            if len(js) in (1, 2):
                note(js[-1], o, name)
            else:
                raise SystemExit(f"{o['name']}: a triangle spans joints {js} (max two)")
    dls, mats = {}, {}
    exports, waiting = {}, set()

    def skey(w0):
        op = w0 >> 24
        return (op, w0 & 0xFFFFFF) if op in (0xDB, 0xE2, 0xE3, 0xD9) else (op,)
    base_state = {}                       # what the prologue leaves set (lights, combiner, colors)
    for o in range(0, len(prologue), 8):
        w0, w1 = struct.unpack_from('>II', prologue, o)
        if w0 >> 24 in (0xDB, 0xFC, 0xFA, 0xE3, 0xE2):
            base_state[skey(w0)] = (w0, w1)
    # what a material without its own value draws with: the model's lights, the game's filter
    base_state[skey(MAT.FILTER_CMD)] = (MAT.FILTER_CMD, MAT.FILTER['bilinear'])
    light_defaults = {k: v for k, v in base_state.items() if k[0] == 0xDB or k == skey(MAT.FILTER_CMD)}

    def setup_fn(mi):
        def setup(dw, mat):
            t = textures.get(mat)
            if not hasattr(dw, 'cur'):
                dw.cur = dict(base_state)
            if dw.blending and not t.special():                     # back to the normal render mode
                dw.raw(GW.g(0xE7000000) + b''.join(GW.g(*c) for c in GW.OPAQUE))
                dw.cur.update({skey(a): (a, b) for a, b in GW.OPAQUE})
                dw.blending = False
            if dw.alpha_compare and not t.cutout:
                dw.raw(GW.g(0xE7000000) + b''.join(GW.g(*c) for c in MAT.ALPHA_COMPARE_OFF))
                dw.cur.update({skey(c[0]): c for c in MAT.ALPHA_COMPARE_OFF})
                dw.alpha_compare = False
            want = dict(light_defaults)           # the model's lights unless the material has its own
            for a, b in t.state_commands():
                want[skey(a)] = (a, b)
            changed = [GW.g(a, b) for k, (a, b) in want.items() if dw.cur.get(k) != (a, b)]
            dw.cur.update(want)
            dw.raw(GW.g(0xE7000000) + b''.join(changed))
            blk, fix = t.block(mi.get(mat, 0))
            dw.raw(blk, [(off, store(t.inline_data(what))) for off, what in fix])
            dw.blending |= t.special()
            # alpha compare is not part of the render mode the game resets: switch it off after
            dw.alpha_compare |= t.cutout or any(a == 0xE2001E01 and b for a, b in t.state)
        return setup

    def free_below():
        return min([min(exports[c].values()) for c in waiting] + [32])
    for j in sorted(per_joint):
        pj = per_joint[j]
        if keep_order:
            def order(names, _a=appear.get(j, {})):
                return sorted(names, key=lambda n: _a.get(n, len(_a)))
        else:
            order = textures.order
        used = order({m for k in ('own', 'cross') for m in pj[k]})
        hint = [m if m is not None and textures.has(m) and textures.get(m).uses_mobj() else None
                for m in (mobj_order or {}).get(j, [])]
        mi = {}
        for i, m in enumerate(hint):
            if m is not None:
                mi.setdefault(m, i)
        # the same original MObj under another material (e.g. the skeleton drawing a body MObj
        # with its own texture setup): its slot
        by_mobj = {textures.get(m).mobj: i for i, m in enumerate(hint) if m is not None and textures.get(m).mobj is not None}
        for m in used:
            t = textures.get(m)
            if t.uses_mobj() and m not in mi and t.mobj is not None and t.mobj in by_mobj:
                o = textures.get(hint[by_mobj[t.mobj]])
                # only when it still is the same MObj (two materials that shared one in the
                # original may have their own palettes / images now)
                if (t.palettes, t.images, t.palette_source, t.image_source, t.mobj_flags()) == \
                        (o.palettes, o.images, o.palette_source, o.image_source, o.mobj_flags()) or \
                        (t.palette_source != 'list' and t.image_source != 'list'):
                    mi[m] = by_mobj[t.mobj]
        mi.update(slots or {})                    # given MObj slots (skeleton: the body's)
        mobj_mats = hint + [m for m in used if textures.get(m).uses_mobj() and m not in mi]
        for i, m in enumerate(mobj_mats):
            if m is not None:
                mi.setdefault(m, i)
        mats[j] = mobj_mats
        dw = GW.DLWriter(blob, prologue)
        dw.blending = False
        dw.alpha_compare = False
        dw.wedges = {k: v[1] for k, v in wedge.items()}
        imp = exports.get(j, {})
        cross = {m: [[('slot', imp[w]) if w in imp else w for w in tri] for tri in tris]
                 for m, tris in pj['cross'].items()}
        missing = [w for tris in cross.values() for tri in tris for w in tri
                   if not isinstance(w, tuple) or w[0] != 'slot']
        if any(wedge[w][0] != j for w in missing):
            raise SystemExit(f'joint {j}: a triangle reaches a joint drawn after it')
        if preserve_order:
            runs = []
            for name, tri, is_cross in ordered[j]:
                if is_cross:
                    missing = [w for w in tri if w not in imp and wedge[w][0] != j]
                    if missing:
                        raise SystemExit(f'joint {j}: a triangle reaches a joint drawn after it')
                    tri = [('slot', imp[w]) if w in imp else w for w in tri]
                if runs and runs[-1][0] == name:
                    runs[-1][1].append(tri)
                else:
                    runs.append([name, [tri]])
            for name, tris in runs:
                GW.emit_tris(dw, {name: tris}, setup_fn(mi), free_below())
            waiting.discard(j)
        else:
            GW.emit_tris(dw, {m: cross[m] for m in order(cross)}, setup_fn(mi), free_below())
            waiting.discard(j)
            GW.emit_tris(dw, {m: pj['own'][m] for m in order(pj['own'])}, setup_fn(mi), free_below())
        # keep the verts later joints' cross triangles need
        for later in sorted(per_joint):
            if later <= j:
                continue
            need = list(dict.fromkeys(w for tris in per_joint[later]['cross'].values()
                                      for tri in tris for w in tri if wedge[w][0] == j))
            if need:
                v0 = free_below() - len(need)
                if v0 < MIN_FREE_SLOTS:
                    raise SystemExit(f'joint {j}: too many shared vertices for joint {later}')
                dw.vtx([dw.wedges[w] for w in need], v0)
                exports.setdefault(later, {}).update({w: v0 + i for i, w in enumerate(need)})
                waiting.add(later)
        if dw.alpha_compare:                     # not reset by the game's render mode
            dw.raw(GW.g(0xE7000000) + b''.join(GW.g(*c) for c in MAT.ALPHA_COMPARE_OFF))
        if epilogue:                             # items / stages: the state the original DL left
            dw.raw(epilogue)
        dls[j] = dw.finish()
    return dls, mats


def set_lights(prologue, lights):
    """Light colors (n64_light_color / n64_shadow_color in the .blend) into the DL prologue."""
    out = bytearray(prologue)
    for o in range(0, len(out), 8):
        w0, w1 = struct.unpack_from('>II', out, o)
        for key, words in (('light', (0xDB0A0000, 0xDB0A0004)), ('shadow', (0xDB0A0018, 0xDB0A001C))):
            if w0 in words and lights.get(key):
                rgb = int(str(lights[key]).lstrip('#'), 16) & 0xFFFFFF
                struct.pack_into('>I', out, o + 4, (rgb << 8) | (w1 & 0xFF))
    return bytes(out)


def prologue_of(f, dl):
    """The base model's DL setup (sync, light colors, lighting, combiner):
    commands before its first texture / vertex / material command."""
    d, out, o = f.char, b'', dl
    while o + 8 <= len(d):
        op = d[o]
        if op in (0x01, 0x05, 0x06, 0xD7, 0xF5, 0xFD, 0xF0, 0xF3, 0xDE, 0xDF, 0xE8, 0xE6):
            break
        if d[o:o + 8] == bytes.fromhex('D9FFFBFF00000000'):
            # what gbi_writer puts right after the prologue: the rest is a model this import
            # wrote (its first material's setup), not the prologue - taking it would add it
            # once more on every import (files imported before keep extra copies until then)
            break
        out += d[o:o + 8]
        o += 8
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('char_dir')
    ap.add_argument('work_dir', help='blend_to_gltf.py output')
    ap.add_argument('--out', help='write main.bin / character.bin here (default: the character folder)')
    ap.add_argument('--main', help='base main.bin (default: CHAR_DIR/main.bin)')
    ap.add_argument('--char', help='base character.bin (default: CHAR_DIR/character.bin)')
    ap.add_argument('--costumes', type=int)
    ap.add_argument('--manifest', help="the export's manifest.json (default: the one the .blend was built from)")
    ap.add_argument('--no-gc', action='store_true', help="don't remove unreachable data")
    ap.add_argument('--keep-render-modes', action='store_true',
                    help="keep cut-out materials' own render modes (default: replaced by alpha compare, "
                         'so the cloaking device fades them like the rest of the fighter)')
    ap.add_argument('--all', action='store_true', help='a full set: the shield pose file and config.yaml (with '
                                                       '--out) written even when unchanged')
    ap.add_argument('--preview', metavar='DIR', help='render what was built (body, expressions, parts, skeleton '
                                                    'sheets) into DIR')
    a = ap.parse_args(argv)
    import export_model as EX
    cfg = EX.read_config(a.char_dir)
    # config.yaml edits: in place; with --out, into a copy there (kept only when something changed:
    # the folder then holds every file to copy into the character's)
    a.cfg_out = os.path.join(a.char_dir, 'config.yaml')
    cfg_orig = None
    if a.out and os.path.exists(a.cfg_out):
        import shutil
        os.makedirs(a.out, exist_ok=True)
        cfg_orig = open(a.cfg_out, 'rb').read()
        shutil.copyfile(a.cfg_out, os.path.join(a.out, 'config.yaml'))
        a.cfg_out = os.path.join(a.out, 'config.yaml')
    off = cfg.get('offsets', {})
    mi_head, me_head = (int(x, 16) for x in off.get('main', ['02CC', '0000']))
    ci_head = int(off.get('character', ['0004'])[0], 16)
    main_path = a.main or os.path.join(a.char_dir, 'main.bin')
    char_path = a.char or os.path.join(a.char_dir, 'character.bin')
    char_data, char_ext, ce_head = EX.folder_external_data(cfg, a.char_dir if not a.char else None,
                                                           open(char_path, 'rb').read())
    if char_ext:   # + the vanilla files its external chain points into, as the export read them
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_char = os.path.join(temp_dir, 'character.bin')
            open(tmp_char, 'wb').write(char_data)
            f = MF.load(main_path, tmp_char, mi_head, me_head, ci_head, char_ext)
    else:
        f = MF.load(main_path, char_path, mi_head, me_head, ci_head)
    try:
        blend_n = json.load(open(os.path.join(a.work_dir, 'costumes.json'))).get('costume_count')
    except (OSError, ValueError):
        blend_n = None
    # costumes: the .blend's (Costume N collections: what the export found in the file's costume scripts,
    # plus / minus N64 Fighter > Add / Remove Costume), else the export's, else config.yaml's
    textures = Materials(a.work_dir, a.costumes or blend_n or cfg.get('num_costumes') or 1, f.char, a.manifest)
    man_n = textures.manifest.get('costumes')
    N = textures.costumes = a.costumes or blend_n or man_n or cfg.get('num_costumes') or 1
    costumes_changed = None
    if blend_n and man_n and blend_n != man_n and not a.costumes:
        cfg_n = cfg.get('num_costumes')
        if cfg_n is None or int(cfg_n) == man_n:
            costumes_changed = (man_n, blend_n)
        else:                   # (config.yaml counts differently: e.g. the Ice Climbers' half)
            print(f'  costumes: {man_n} -> {blend_n} in the file; config.yaml num_costumes ({cfg_n}) counts them '
                  'differently: not changed (update it by hand if needed)')
    if costumes_changed:
        old_n, new_n = costumes_changed
        line = f'num_costumes: {new_n}'
        if a.cfg_out:
            raw_ = open(a.cfg_out, encoding='utf-8', newline='').read()
            nl_ = '\r\n' if '\r\n' in raw_ else '\n'
            open(a.cfg_out, 'w', encoding='utf-8', newline='').write(
                GD_set_yaml(raw_.replace('\r\n', '\n'), 'num_costumes', [line]).replace('\n', nl_))
        print(f'  costumes: {old_n} -> {new_n} (config.yaml {line})')
        dc = cfg.get('default_costumes') or []
        if any(int(x) >= new_n for x in dc):
            print(f'  note: config.yaml default_costumes {dc} names costumes past the last one ({new_n - 1})')
        missing = [k for k in range(new_n) if not os.path.exists(os.path.join(a.char_dir, f'stock_icon_{k}.png'))]
        if any(os.path.exists(os.path.join(a.char_dir, f'stock_icon_{k}.png')) for k in range(new_n)) and missing:
            print(f'  note: no stock_icon_{missing[0]}.png' + (f' ... ({len(missing)})' if len(missing) > 1 else '')
                  + ': the build uses the stock icon from the files for those costumes')
    textures.cloak_safe = not a.keep_render_modes
    joints = f.details['hi']['joints']
    W = [j.world for j in joints]
    # the model's DL setup: from the first display list that has one (some joints have an
    # empty DL); never a render mode (the game sets it: cloaking device)
    prologue = next((p for p in (prologue_of(f, j.dl) for j in joints if j.dl is not None) if len(p) >= 16), b'')
    prologue = b''.join(prologue[o:o + 8] for o in range(0, len(prologue), 8)
                        if struct.unpack_from('>I', prologue, o)[0] != 0xE200001C)
    if len(prologue) < 16:
        raise SystemExit('could not read the base model DL prologue')
    prologue = set_lights(prologue, textures.lights)

    # attributes that name joints (Bone > N64 Joint, Armature > N64 Fighter Joints): what changed
    import gamedata as GD
    import jointattrs as JAT
    gd_ = GD._load_work(a.work_dir) or {}
    orig_ja = (getattr(textures, 'manifest', None) or {}).get('joint_attrs')
    # joints removed in Blender (Bone > N64 Joint > Remove Joint): everything indexed by joint is
    # renumbered (jointmap.py); the model is built in the old numbering, then compacted
    import jointmap as JM
    try:
        fj = json.load(open(os.path.join(a.work_dir, 'costumes.json'))).get('fighter_joints') or {}
    except (OSError, ValueError):
        fj = {}
    added = {int(t): v for t, v in (fj.get('added') or {}).items()}
    parents = {int(x): (None if p is None else int(p)) for x, p in (fj.get('parents') or {}).items()}
    if fj.get('order'):                       # joints added / moved (and maybe removed) in Blender
        jm = JM.make_order(f, fj['order'], added, parents=parents,
                           modes={int(x): m for x, m in (fj.get('moved_modes') or {}).items()})
    elif fj.get('removed'):
        jm = JM.make(f, fj['removed'])
    else:
        jm = JM.JointMap(len(joints))
    if jm:
        print(f'  joints {jm.describe()} ({jm.n_old} -> {jm.n_new} joints; '
              + ', '.join(f'joint_{o:02d} -> joint_{n:02d}' for o, n in jm.renumbered()[:4])
              + (' ...' if len(jm.renumbered()) > 4 else '') + ')')
    new_bits = {k: {t: v[k] for t, v in added.items()} for k in ('setup', 'animlock')}
    new_ja = JM.remap_attrs(gd_.get('joint_attrs'), jm, new_bits) if jm else gd_.get('joint_attrs')
    if jm and new_ja and new_ja.get('setup'):
        jm.tree, jm.depth = jm.tree_data(f, new_ja['setup'], new_ja.get('hidden'))  # (Blender's setup bits / hidden parts)
    elif jm and not hasattr(jm, 'tree'):
        jm.tree, jm.depth = jm.tree_data(f, JM.remap_bits([0xFFFFFFFF, 0xFFFFFFFF], jm))
    # the added joints' rest matrices after the old ones (the meshes are built in the old numbering,
    # added joints at their temporary numbers)
    W_ext = [j.world for j in joints] + [jm.worlds[t] for t in sorted(added)] if added else None
    ja_plan = JAT.plan(f, new_ja, orig_ja) if new_ja and orig_ja else JAT.Plan()

    # model part variants: joints whose variant count changed (or whose list the import already
    # moved to character.bin) get a new list in character.bin; the others keep theirs, re-pointed
    part_counts = {}
    if os.path.exists(os.path.join(a.work_dir, 'parts.gltf')):
        for nd in json.load(open(os.path.join(a.work_dir, 'parts.gltf'))).get('nodes', []):
            m_ = re.match(r'part(\d+)_(hi|lo)_j(\d+)$', nd.get('name', ''))
            if m_ and 'mesh' in nd:
                part_counts[int(m_.group(3))] = max(part_counts.get(int(m_.group(3)), 0), int(m_.group(1)) + 1)
    mp_ = f.ptr_main(f.attr + MF.A_MODELPARTS)
    part_rebuild = {j for j, n in part_counts.items()
                    if len(f.modelparts.get(j, [])) != n or
                    any(p['field'] is None for pair in f.modelparts.get(j, []) for p in pair)}
    if part_rebuild and (mp_ is None or mp_[0] != 'main'):
        raise SystemExit('model parts: the fighter has no model part container in main.bin; new variants '
                         f'(joints {sorted(part_rebuild)}) need one')
    n_old_joints = len(joints)
    part_unlink = [mp_[1] + 4 * j for j in sorted(part_rebuild) if j < n_old_joints and mp_[1] + 4 * j in f.main_int]
    new_joint_parts = {}            # added joint (temporary index) -> its variant list (character.bin)

    def build(base_len, quiet=False):
        """The new model as data appended at base_len -> (blob, {main.bin field: new target})."""
        def say(*x):
            if not quiet:
                print(*x)
        blob = GW.Blob(base_len + (-base_len) % 8)
        retarget = {}                                        # main.bin external field -> new char target
        for fld_, data_ in ja_plan.new.items():              # joint attribute tables moved / added
            retarget[fld_] = blob.add(data_, 4)
        body_mats = {}                                       # detail -> {joint: MObj list order}

        # ---- materials: MObjSub (original, or a template) + image / palette lists + costume scripts
        template_ms = None
        for lst in f.details['hi']['mobjs'].values():
            for ms in lst:
                if MF.u16(f.char, ms + 0x30) & 0x0005 == 0x0005:
                    template_ms = ms
                    break
            if template_ms:
                break
        if template_ms is None:                # no image+palette list MObj: any image or palette list one
            template_ms = next((ms for lst in f.details['hi']['mobjs'].values() for ms in lst
                                if MF.u16(f.char, ms + 0x30) & 0x0005), None)
        mobj_of, script_of = {}, {}
        data_at, mobj_at = {}, {}                          # dedup: identical bytes / materials stored once

        def store(data, align=8):
            if data not in data_at:
                data_at[data] = blob.add(data, align)
            return data_at[data]

        def ptr_list(targets):
            lst = blob.add(b'\0' * (4 * len(targets)), 4)
            for i, t in enumerate(targets):
                if t is not None:                  # (None: an empty slot, stays 0)
                    blob.ptr(lst + 4 * i, t)
            return lst

        def mobjsub(name):
            if name in mobj_of:
                return mobj_of[name]
            if name is None:                          # unused slot: an MObj that only sets the prim color
                ms = bytearray(f.char[template_ms:template_ms + MOBJSUB_SIZE])
                struct.pack_into('>I', ms, 0x04, 0)
                struct.pack_into('>I', ms, 0x2C, 0)
                struct.pack_into('>H', ms, 0x30, 0x0200)
                mobj_of[None], script_of[None] = blob.add(bytes(ms), 8), None
                return mobj_of[None]
            t = textures.get(name)
            imgs = tuple(store(x) if x is not None else None for x in t.images) if t.image_source == 'list' else ()
            pals = tuple(store(p) for p in t.palettes) if t.palette_source == 'list' else ()
            src = t.mobj if t.mobj is not None else template_ms
            if src is None:
                raise SystemExit('no image+palette material in the base file to use as a template')
            ms = bytearray(f.char[src:src + MOBJSUB_SIZE])
            struct.pack_into('>I', ms, 0x04, 0)
            struct.pack_into('>I', ms, 0x2C, 0)
            struct.pack_into('>H', ms, 0x30, t.mobj_flags())
            for k, v in ((t.costume_colors or [{}])[0]).items():     # costume 0's colors as the base
                struct.pack_into('>I', ms, MAT.COLOR_FIELDS[k], int(v, 16))
            if t.new and not t.untextured:
                # image SETTIMG (sub.fmt / siz: the load size), TLUT size (sub.siz == 8b: 256), block fmt / siz
                struct.pack_into('>BB', ms, 0x02, t.fmt, 3 if t.siz == 3 else (1 if t.siz == 1 and not imgs else 2))
                struct.pack_into('>BB', ms, 0x32, t.fmt, t.siz)
            key = (bytes(ms), imgs, pals)
            if key not in mobj_at:
                o = blob.add(bytes(ms), 8)
                if imgs:
                    blob.ptr(o + 0x04, ptr_list(imgs))
                if pals:
                    blob.ptr(o + 0x2C, ptr_list(pals))
                mobj_at[key] = o
            mobj_of[name] = mobj_at[key]
            sc = t.script(N)
            script_of[name] = store(sc, 4) if sc else None               # identical scripts are shared
            return mobj_of[name]

        def lists_for(mats_per_joint, nj):
            nj = jm.n_new if jm else nj
            p_ms = blob.add(b'\0' * (4 * nj), 4)
            p_an = blob.add(b'\0' * (4 * nj), 4)
            for j_old, names in mats_per_joint.items():
                j = jm.idx(j_old)
                if j is None:
                    if names:
                        raise SystemExit(f'joint_{j_old:02d} is removed but still has meshes ({detail})')
                    continue
                lst = blob.add(b'\0' * (4 * (len(names) + 1)), 4)
                an = blob.add(b'\0' * (4 * len(names)), 4)
                for i, n in enumerate(names):
                    blob.ptr(lst + 4 * i, mobjsub(n))
                    if script_of[n] is not None:
                        blob.ptr(an + 4 * i, script_of[n])
                blob.ptr(p_ms + 4 * j, lst)
                blob.ptr(p_an + 4 * j, an)
            return p_ms, p_an

        cp = f.ptr_main(f.attr + MF.A_COMMONPARTS)[1]
        for k, (detail, gltf) in enumerate((('hi', 'body.gltf'), ('lo', 'lo.gltf'))):
            path = os.path.join(a.work_dir, gltf)
            d = f.details.get(detail)
            if d is None or not os.path.exists(path):
                continue
            objs = load_gltf(path)
            order = {int(j): v for j, v in (textures.manifest.get('mobj_order') or {}).get(detail, {}).items()}
            dls, mats = build_joints(objs, W_ext or [j.world for j in d['joints']], textures, blob, prologue,
                                     store, mobj_order=order)
            p_ms, p_an = lists_for(mats, len(d['joints']))
            body_mats[detail] = mats
            arr = bytearray(f.char[d['dobjdesc']:d['dobjdesc'] + MF.DOBJDESC_SIZE * len(d['joints']) + 4])
            for j in range(len(d['joints'])):
                struct.pack_into('>I', arr, MF.DOBJDESC_SIZE * j + 4, 0)
            if jm:                                # the new order: removed entries out, added ones in
                def entry(x, k):
                    if x < len(d['joints']):
                        e = bytearray(arr[MF.DOBJDESC_SIZE * x:MF.DOBJDESC_SIZE * (x + 1)])
                        struct.pack_into('>I', e, 0, jm.depth[k])             # (moved branches: new depths)
                        if x in jm.moved:                                    # its rest under the new parent
                            r_ = jm.moved_rest[x]
                            struct.pack_into('>9f', e, 8, r_['tx'], r_['ty'], r_['tz'], r_['rx'], r_['ry'], r_['rz'],
                                             r_['sx'], r_['sy'], r_['sz'])
                        return bytes(e)
                    return struct.pack('>II9f', jm.depth[k], 0, *jm.added[x]['t'], 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
                arr = b''.join(entry(x, k) for k, x in enumerate(jm.order)) + arr[-4:]
            do = blob.add(bytes(arr), 8)
            for j, dl in dls.items():
                if jm.idx(j) is None:
                    raise SystemExit(f'joint_{j:02d} is removed but still has a mesh ({detail})')
                blob.ptr(do + MF.DOBJDESC_SIZE * jm.idx(j) + 4, dl)
            base = cp + 0x10 * k
            retarget[base] = do
            retarget[base + 4] = p_ms
            retarget[base + 8] = p_an
            tris = sum(len(o['tris']) for o in objs)
            say(f'  {detail}: {tris} triangles on joints {sorted(dls)}')

        # ---- model parts: objects part<variant>_<hi|lo>_j<joint>
        parts_path = os.path.join(a.work_dir, 'parts.gltf')
        built_parts = {}                                      # (joint, variant, detail) -> (dl, mobjsubs, scripts)
        if os.path.exists(parts_path):
            n_parts = 0
            for o in load_gltf(parts_path):
                m = re.match(r'part(\d+)_(hi|lo)_j(\d+)$', o['name'])
                if not m:
                    say(f"  warning: {o['name']}: not a part<variant>_<hi|lo>_j<joint> object, skipped")
                    continue
                v, detail, joint = int(m.group(1)), m.group(2), int(m.group(3))
                slots = f.modelparts.get(joint, [])
                fld = slots[v][0 if detail == 'hi' else 1]['field'] \
                    if v < len(slots) and joint not in part_rebuild else None
                part_info = next((p for p in textures.manifest.get('model_parts', [])
                                  if p['joint'] == joint and p['variant'] == v and p['detail'] == detail), {})
                dls, mats = build_joints([o], W_ext or W, textures, blob, prologue, store, only_joint=joint,
                                         mobj_order={joint: part_info.get('mobj_order') or []})
                lst = blob.add(b'\0' * (4 * (len(mats[joint]) + 1)), 4)
                an = blob.add(b'\0' * (4 * len(mats[joint])), 4)
                for i, n in enumerate(mats[joint]):
                    blob.ptr(lst + 4 * i, mobjsub(n))
                    if script_of[n] is not None:
                        blob.ptr(an + 4 * i, script_of[n])
                has_scripts = any(script_of[n] is not None for n in mats[joint])
                built_parts[(joint, v, detail)] = (dls[joint], lst if mats[joint] else None,
                                                   an if has_scripts else None)
                if fld is not None:                                         # the base file's variant
                    retarget[fld] = dls[joint]
                    if mats[joint] or fld + 4 in f.main_ext:             # materials list (none: inline textures only)
                        retarget[fld + 4] = lst
                    if has_scripts or fld + 8 in f.main_ext:
                        retarget[fld + 8] = an                              # costume scripts, by material index
                n_parts += 1
            say(f'  model parts: {n_parts} rebuilt')
            # variant 0 is what a joint starts with (the game sets model part id 0 at creation, and goes
            # back to variant 0's mesh when parts reset): it has to look like the body's piece
            if not quiet and os.path.exists(os.path.join(a.work_dir, 'body.gltf')):
                body_ = {o['name']: o for o in load_gltf(os.path.join(a.work_dir, 'body.gltf'))}
                for o in load_gltf(parts_path):
                    m0 = re.match(r'part0_hi_j(\d+)$', o['name'])
                    if not m0:
                        continue
                    b_ = body_.get(f'hi_j{m0.group(1)}')
                    sig = lambda x: sorted(tuple(round(float(c), 1) for c in p) for p in x['pos'])
                    if b_ is not None and sig(b_) != sig(o):
                        say(f"  warning: joint_{int(m0.group(1)):02d}'s model part variant 0 differs from its body piece: "
                            'the fighter starts with the body piece as model part id 0, so an event picking variant 0 '
                            'changes nothing, and resetting parts shows variant 0. Keep variant 0 = the body; put the '
                            'new look in variant 1+')
            # new variant lists (FTModelPartDesc: [variant][hi, lo] FTModelPart, 0x14 bytes each:
            # dl, mobjsubs, costume scripts, main matanim (unused), u8 flags), an empty pair after
            for joint in sorted(part_rebuild):
                n = part_counts[joint]
                slots = f.modelparts.get(joint, [])
                raw = bytearray(MF.MODELPART_SIZE * 2 * (n + 1))
                for v in range(n):
                    for k in range(2):
                        src = slots[v][k] if v < len(slots) else (slots[0][k] if slots else None)
                        raw[MF.MODELPART_SIZE * (2 * v + k) + 0x10] = src['flags'] if src else 0
                desc = blob.add(bytes(raw), 4)
                for v in range(n):
                    for k, detail in enumerate(('hi', 'lo')):
                        e = desc + MF.MODELPART_SIZE * (2 * v + k)
                        got = built_parts.get((joint, v, detail))
                        if got is None:
                            say(f'  warning: joint {joint} variant {v}: no {detail} object (part{v}_{detail}_j{joint:02d}): '
                                f'{"nothing drawn in that detail" if detail == "lo" else "empty"}')
                            continue
                        for o_, t_ in zip((0, 4, 8), got):
                            if t_ is not None:
                                blob.ptr(e + o_, t_)
                if joint >= n_old_joints:      # an added joint: its container slot exists after the rebuild
                    new_joint_parts[joint] = desc
                else:
                    retarget[mp_[1] + 4 * joint] = desc
                old_n = len(slots)
                say(f'  model parts: joint {joint}: {old_n} -> {n} variants (list written to character.bin)')

        # ---- electric-shock skeleton: objects skel<id>_j<joint>; drawn with the joint's body MObjs
        skel_path = os.path.join(a.work_dir, 'skeleton.gltf')
        if os.path.exists(skel_path):
            n_skel = 0
            for o in load_gltf(skel_path):
                m = re.match(r'skel(\d+)_j(\d+)$', o['name'])
                ent = f.skeletons.get(int(m.group(1)), {}).get(int(m.group(2))) if m else None
                if ent is None:
                    say(f"  warning: {o['name']}: no such skeleton entry in the base file (skel<id>_j<joint>), skipped")
                    continue
                joint = int(m.group(2))
                body = body_mats.get('hi', {}).get(joint, [])
                info = next((x for x in textures.manifest.get('skeletons', []) if x['object'] == o['name']), {})
                dls, mats = build_joints([o], W, textures, blob, prologue, store, only_joint=joint,
                                         mobj_order={joint: body}, slots=info.get('mobj_slots'))
                extra = [n for n in mats[joint] if n not in body]
                if extra:
                    raise SystemExit(f"{o['name']}: materials {extra} come from MObjs, but the skeleton can only "
                                     f"use the body's MObjs on joint {joint} ({body}) or inline textures")
                retarget[ent['field']] = dls[joint]
                n_skel += 1
            say(f'  electric skeleton: {n_skel} rebuilt')
        return blob, retarget

    reqlist_raw = open(os.path.join(a.char_dir, 'main_reqlist.txt'), 'rb').read()
    newline = '\r\n' if b'\r\n' in reqlist_raw else '\n'
    reqlist_lines = reqlist_raw.decode('utf-8').splitlines()
    extend = int(cfg.get('extend_main_reqlist', 0))                  # last lines: extra files, no pointer

    def char_fields_of(main_bytes, lines):
        reqlist = [ln.strip() for ln in lines if not ln.startswith('END OF')]
        ext_fields = list(MF.chain(bytes(main_bytes), me_head).keys())
        if len(reqlist) != len(ext_fields) + extend:
            raise SystemExit(f'main_reqlist.txt has {len(reqlist)} lines but main.bin has '
                             f'{len(ext_fields)} external pointers (+{extend} extend_main_reqlist)')
        return [fld for fld, line in zip(ext_fields, reqlist) if '${CHARACTER}' in line]

    # pass 1: which main.bin fields get the new model; then drop the old model from the base
    # first (keeps character.bin small enough for its 16-bit offsets while both would exist)
    _, replaced = build(len(f.char), quiet=True)
    main = bytearray(f.main)
    char = bytearray(f.char)
    mi_head_base, me_head_base = mi_head, me_head
    for fld_ in ja_plan.unlink + part_unlink:    # tables that move to character.bin
        mi_head = JAT.unlink_internal(main, mi_head, fld_)    # (f keeps reading the base file's tables)
    # the old variant lists of joints whose list moved: their pointers into character.bin leave
    # main.bin's external chain (and main_reqlist.txt), or what they point at would be kept
    for fld_ in [p['field'] + o for j in sorted(part_rebuild) for pair in f.modelparts.get(j, []) for p in pair
                 if p['field'] is not None for o in (0, 4, 8, 0xC) if p['field'] + o in f.main_ext]:
        ext_ = list(MF.chain(bytes(main), me_head).keys())
        if fld_ not in ext_:
            continue
        lines_ = [i for i, ln in enumerate(reqlist_lines) if not ln.startswith('END OF')]
        del reqlist_lines[lines_[ext_.index(fld_)]]
        me_head = JAT.unlink_internal(main, me_head, fld_)
    if not a.no_gc:
        keep = [fld for fld in char_fields_of(main, reqlist_lines) if fld not in replaced]
        res = ge.gc(bytes(char), [0, ci_head] + [f.main_ext[fld] for fld in keep], head=ci_head)
        if res.new_head != ci_head:
            raise SystemExit(f'character.bin chain head moved 0x{ci_head:X} -> 0x{res.new_head:X}')
        for fld in keep:
            struct.pack_into('>H', main, fld + 2, res.remap[f.main_ext[fld]] // 4)
        print(f'  old model removed: {len(char)} -> {len(res.data)} bytes')
        # character.bin's external pointers (into vanilla files, appended above) must all have gone
        # with the old model: the new one copies what it uses (the chain isn't rebuilt)
        alive = [fld for fld in char_ext if res.remap.get(fld) is not None]
        if alive:
            raise SystemExit('character.bin external pointers outside the model survive at '
                             + ', '.join(f'0x{x:X}' for x in alive) + ': not supported (its external chain)')
        char = bytearray(res.data)
    blob, retarget = build(len(char))

    # ---- write character.bin (+ new data), re-point main.bin
    newc = GW.append_blob(char + b'\0' * ((-len(char)) % 8), blob, ci_head, lambda d, h: MF.chain(d, h))
    added = 0
    for fld, tgt in sorted(retarget.items()):
        if fld not in f.main_ext:
            if MF.u32(main, fld) != 0:
                raise SystemExit(f'main.bin 0x{fld:X} holds data, not an (empty) pointer field')
            # new pointer into character.bin: append it to main.bin's external chain and
            # add its file (${CHARACTER}) at the end of the reqlist, in the same order
            ext_tail = list(MF.chain(bytes(main), me_head).keys())[-1]
            struct.pack_into('>H', main, ext_tail, fld // 4)
            struct.pack_into('>H', main, fld, 0xFFFF)
            end = next((i for i, ln in enumerate(reqlist_lines) if ln.startswith('END OF')), len(reqlist_lines))
            reqlist_lines.insert(end - extend, '${CHARACTER}')      # before the extend_main_reqlist lines
            f.main_ext[fld] = tgt
            added += 1
        struct.pack_into('>H', main, fld + 2, tgt // 4)
    if added:
        print(f'  {added} new pointers into character.bin (main_reqlist.txt gets {added} more lines)')
    for fld_ in ja_plan.new:              # f stays the base fighter (its tables: the base files')
        if fld_ in f.main_int or f.ptr_main(fld_) is None or fld_ in ja_plan.unlink or \
                MF.u32(f.main, fld_) == 0:
            f.main_ext.pop(fld_, None)

    # ---- removed joints: main.bin's per-joint tables lose their entries (the chains and
    # main_reqlist.txt follow)
    if jm:
        req_idx = [i for i, ln in enumerate(reqlist_lines) if not ln.startswith('END OF')]
        n_ext = len(MF.chain(bytes(main), me_head))
        ext_lines = [reqlist_lines[i] for i in req_idx[:n_ext]]
        reloc = JM.Relocator(main, mi_head, me_head, ext_lines)
        what, at_ = JM.main_tables(f, jm, reloc)
        for t, desc in sorted(new_joint_parts.items()):          # added joints' variant lists
            if 'modelparts' not in at_:
                raise SystemExit('model parts on an added joint: the fighter has no model part container')
            reloc.add_ext(at_['modelparts'] + 4 * jm.idx(t), desc, '${CHARACTER}')
            what.append(f'joint_{jm.idx(t):02d} model parts')
        main_, mi_head, me_head, ext_lines = reloc.finish()
        main = bytearray(main_)
        rest = [reqlist_lines[i] for i in req_idx[n_ext:]]
        tail = reqlist_lines[req_idx[-1] + 1:] if req_idx else reqlist_lines
        reqlist_lines = ext_lines + rest + tail
        print('  main.bin per-joint tables: ' + (', '.join(what) or 'none'))

    # ---- remove what nothing reaches anymore (roots: main.bin's pointers into character.bin)
    if not a.no_gc:
        char_fields = char_fields_of(main, reqlist_lines)
        roots = [0, ci_head] + [MF.u16(main, fld + 2) * 4 for fld in char_fields]
        res = ge.gc(bytes(newc), roots, head=ci_head)
        if res.new_head != ci_head:
            raise SystemExit(f'character.bin chain head moved 0x{ci_head:X} -> 0x{res.new_head:X}')
        for fld in char_fields:
            t = MF.u16(main, fld + 2) * 4
            struct.pack_into('>H', main, fld + 2, res.remap[t] // 4)
        print('  ' + res.report[2])
        newc = res.data
    if len(newc) > 0x3FFFC:
        raise SystemExit(f'character.bin is {len(newc)} bytes: over the 0x3FFFC limit')
    for off_, data_ in ja_plan.writes:
        main[off_:off_ + len(data_)] = data_
    if ja_plan.config_texture_forms is not None:      # (config.yaml texture_forms: the build writes them)
        cfg_path = a.cfg_out
        raw = open(cfg_path, encoding='utf-8', newline='').read()
        text = raw.replace('\r\n', '\n')
        blk = GD.yaml_block(text, 'attributes.texture_forms')
        old_line = text.split('\n')[blk[0]] if blk else ''
        c_ = re.search(r'\s#.*$', old_line)
        lines = ([f'texture_forms: [{", ".join(chr(34) + x + chr(34) for x in ja_plan.config_texture_forms)}]'
                  + (c_.group(0) if c_ else '')] if ja_plan.config_texture_forms else [])
        if a.cfg_out:
            nl = '\r\n' if '\r\n' in raw else '\n'
            open(cfg_path, 'w', encoding='utf-8', newline='').write(
                GD.set_yaml(text, 'attributes.texture_forms', lines).replace('\n', nl))
            print('  config.yaml attributes: ' + (lines[0].split(' #')[0] if lines else 'texture_forms removed'))
        else:
            print('  config.yaml attributes needs (not updated: --out): '
                  + (lines[0].split(' #')[0] if lines else 'texture_forms removed'))
    if ja_plan.notes:
        print('  joint attributes: ' + '; '.join(ja_plan.notes))
    elif gd_.get('joint_attrs'):
        print('  joint attributes: unchanged')
    # hurtboxes (the .blend's Hurtboxes): main.bin, and config.yaml's list when it has one
    msg = GD.apply_hurtboxes(f, main, cfg, a.work_dir, getattr(textures, 'manifest', None) or {},
                             a.cfg_out, write_cfg=True, jm=jm if jm else None)
    if msg:
        print('  ' + msg)
    import legs
    # slope foot IK: ids that don't point at legs (not changed); (joints removed: the ids are the
    # new numbering, f the old - checked before, in Blender's bones)
    note = None if jm else legs.check(f, main)[3]
    if note:
        print('  note: ' + note)
    out = a.out or a.char_dir
    os.makedirs(out, exist_ok=True)
    shield = apply_shield_pose(f, main, cfg, a, out, me_head, reqlist_lines, jm=jm if jm else None)
    open(os.path.join(out, 'character.bin'), 'wb').write(newc)
    open(os.path.join(out, 'main.bin'), 'wb').write(main)
    if char_ext:   # the external chain is gone (its data copied in): no external head, no reqlist
        line = f'character: ["{ci_head:04X}", "3FFFC"]'
        if a.out is None:
            cfg_path = os.path.join(a.char_dir, 'config.yaml')
            raw = open(cfg_path, encoding='utf-8', newline='').read()
            nl = '\r\n' if '\r\n' in raw else '\n'
            text = GD.set_yaml(raw.replace('\r\n', '\n'), 'offsets.character', [line])
            open(cfg_path, 'w', encoding='utf-8', newline='').write(text.replace('\n', nl))
            os.remove(os.path.join(a.char_dir, 'character_reqlist.txt'))
            print(f'  config.yaml offsets: {line}; character_reqlist.txt removed '
                  '(its files are copied into character.bin now)')
        else:
            print(f'  config.yaml offsets needs (not updated: --out): {line}, and no '
                  'character_reqlist.txt (its files are copied into character.bin now)')
    open(os.path.join(out, 'main_reqlist.txt'), 'w', encoding='utf-8', newline='').write(
        newline.join(reqlist_lines) + (newline if reqlist_raw.endswith(b'\n') else ''))
    print(f'wrote {out}/character.bin ({len(newc)} bytes), main.bin, main_reqlist.txt')
    if (mi_head, me_head) != (mi_head_base, me_head_base):   # a chain's first pointer moved away
        line = f'main: ["{mi_head:04X}", "{me_head:04X}"]'
        path = a.cfg_out
        if path:
            raw = open(path, encoding='utf-8', newline='').read()
            nl = '\r\n' if '\r\n' in raw else '\n'
            text = GD.set_yaml(raw.replace('\r\n', '\n'), 'offsets.main', [line])
            open(path, 'w', encoding='utf-8', newline='').write(text.replace('\n', nl))
            print(f'  config.yaml offsets: {line} (main.bin\'s pointer chains start elsewhere now)')
        else:
            print(f'  config.yaml offsets needs (not updated: --out): {line}')
    if shield is not None:
        open(os.path.join(out, 'shield_pose.bin'), 'wb').write(shield['data'])
        print(f"wrote {out}/shield_pose.bin ({len(shield['data'])} bytes)")
    if cfg_orig is not None:
        if open(a.cfg_out, 'rb').read() == cfg_orig and not a.all:
            os.remove(a.cfg_out)                       # (unchanged: nothing to copy)
        else:
            print(f'wrote {a.cfg_out}')
    # fighter animations edited in Blender (blend_to_gltf.py samples the ones that changed)
    try:
        changed = json.load(open(os.path.join(a.work_dir, 'costumes.json'))).get('fighter_anims') or []
    except (OSError, ValueError):
        changed = []
    if changed or jm:
        import anim_import
        anim_import.apply(a.char_dir, out, changed, getattr(textures, 'manifest', None) or {}, cfg, f,
                          jm=jm if jm else None)
    else:
        print('  animations: none changed')
    if jm:
        import moveset_remap
        moveset_remap.apply(a.char_dir, out, cfg, jm)
    built = MF.load(os.path.join(out, 'main.bin'), os.path.join(out, 'character.bin'), mi_head, me_head, ci_head)
    check_cloaking(cfg, built)
    if a.preview:                     # what was built, read back like any fighter: export it, render that
        import shutil
        import tempfile
        import export_model as EXP
        import preview
        tmp = tempfile.mkdtemp(prefix='fighter_preview_')
        try:
            EXP.Exporter(built, tmp, N).run()
            for p in preview.save_sheets(tmp, a.preview):
                print(f'preview: {p}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)



def apply_shield_pose(f, main, cfg, a, out, me_head, reqlist_lines, jm=None):
    """The .blend's shield pose (blend_to_gltf.shield_pose) -> a new shield_pose.bin
    (shieldpose.build_file) when it changed: main.bin's 0x2D8..0x2F8 pointers (in place) point
    into it, their main_reqlist.txt lines become ${SHIELD_POSE} (the build's ID for the
    character's own file), config.yaml shield_pose: [its internal head, 3FFFC]. The Shield
    Bubble's size -> shield_size (attr + 0x74). Returns the new file's dict or None."""
    import export_model as EX
    import shieldpose as SP
    import jointmap as JM
    try:
        blend = json.load(open(os.path.join(a.work_dir, 'costumes.json'))).get('shield_pose')
    except (OSError, ValueError):
        blend = None
    if not blend and not jm:
        return None
    blend = blend or {}
    r = blend.get('radius')
    old_r = MF.f32(main, f.attr + SP.A_SHIELD_SIZE)
    in_cfg = (cfg.get('attributes') or {}).get('shield_size')
    if in_cfg is not None:
        old_r = float(in_cfg)                 # (the build writes config.yaml's over main.bin's)
    if r and abs(r - old_r) > 0.01:
        struct.pack_into('>f', main, f.attr + SP.A_SHIELD_SIZE, r)
        print(f'  shield size: {old_r:g} -> {r:g}')
        if in_cfg is not None:
            line = f'shield_size: {round(r, 3)}'
            if a.cfg_out:
                import gamedata as GD
                path = a.cfg_out
                raw = open(path, encoding='utf-8', newline='').read()
                nl = '\r\n' if '\r\n' in raw else '\n'
                text = GD.set_yaml(raw.replace('\r\n', '\n'), 'attributes.shield_size', [line])
                open(path, 'w', encoding='utf-8', newline='').write(text.replace('\n', nl))
                print(f'  config.yaml attributes: {line}')
            else:
                print(f'  config.yaml attributes needs (not updated: --out): {line}')
    old = EX.folder_shield_pose(cfg, a.char_dir, quiet=True)
    if old is None:
        print('  shield pose: no file to start from (config.yaml shield_pose / the original ROM): not written')
        return None
    sp = SP.load(f, *old[:2])
    if jm is not None and jm.moved:
        # moved joints: their shield pose relative to the new parent (unless edited in Blender: then
        # Blender's, already relative to it)
        mv = JM.shield_moved(f, jm, sp)
        ed = {tuple(e) for e in (blend.get('edited') or [])}
        blend = dict(blend, turn=dict(blend.get('turn') or {}), neutral=dict(blend.get('neutral') or {}),
                     edited=list(blend.get('edited') or []))
        for nm in mv['turn']:
            if ('turn', nm) not in ed:
                blend['turn'][nm] = mv['turn'][nm]
                blend['neutral'][nm] = mv['neutral'][nm]
                blend['edited'] += [['turn', nm], ['neutral', nm]]
    res = SP.build_file(sp, blend, old[0]) if blend.get('turn') else None
    if jm:
        # removed joints: the file's per-joint arrays lose their entries in place (the offsets main.bin
        # and the motion table use stay)
        import jointmap as JM
        import motions as MO
        import figatree as FT_
        base = res or dict(data=old[0], int_head=old[1], lookup=f.main_ext[f.attr + SP.A_LOOKUP],
                           sectors=[f.main_ext[f.attr + SP.A_SECTORS + 4 * k] for k in range(SP.N_SECTORS)],
                           changed=[], notes=[])
        cid = MO.CHAR_IDS.get(str((cfg.get('definitions') or {}).get('base_character')).upper())
        guard = [(m['file_id'], m['flags']) for m in (MO.rom_motions(cid).values() if cid is not None else [])
                 if m['flags'] & FT_.FLAG_SHIELDPOSE]
        data_, ih_, look_, secs_ = JM.remap_shield_pose(f, base['data'], base['int_head'], jm, guard,
                                                         base['lookup'], base['sectors'])
        res = dict(base, data=data_, int_head=ih_, lookup=look_, sectors=secs_,
                   changed=list(base['changed']) + [f'joints {jm.describe()}'])
    if res is None and a.all and old[2] == 'shield_pose.bin':
        print('  shield pose: unchanged (its file written as it is: --all)')
        return dict(data=old[0])
    if res is None:
        print('  shield pose: unchanged')
        return None
    fields = [f.attr + SP.A_LOOKUP] + [f.attr + SP.A_SECTORS + 4 * k for k in range(SP.N_SECTORS)]
    targets = [res['lookup']] + res['sectors']
    ext = list(MF.chain(bytes(main), me_head))
    req = [i for i, ln in enumerate(reqlist_lines) if not ln.startswith('END OF')]
    for fld, tgt in zip(fields, targets):
        if fld not in ext:
            raise SystemExit(f'main.bin 0x{fld:X} is not an external pointer: the shield pose can\'t be pointed at')
        struct.pack_into('>H', main, fld + 2, tgt // 4)
        reqlist_lines[req[ext.index(fld)]] = '${SHIELD_POSE}'
    print(f"  shield pose ({old[2]}): {len(res['changed'])} change(s): " + ', '.join(res['changed'][:8])
          + (' ...' if len(res['changed']) > 8 else ''))
    for n in res['notes']:
        print(f'    {n}')
    want = [f"{res['int_head']:04X}", '3FFFC']
    have = cfg.get('shield_pose')
    if not (isinstance(have, list) and [str(x).upper() for x in have] == want):
        line = f'shield_pose: ["{want[0]}", "{want[1]}"]'
        if a.cfg_out:
            import gamedata as GD
            path = a.cfg_out
            raw = open(path, encoding='utf-8', newline='').read()
            nl = '\r\n' if '\r\n' in raw else '\n'
            text = GD.set_yaml(raw.replace('\r\n', '\n'), 'shield_pose', [line])
            open(path, 'w', encoding='utf-8', newline='').write(text.replace('\n', nl))
            print(f'  config.yaml: {line}')
        else:
            print(f'  config.yaml needs (not updated: --out): {line}')
    return res

def check_cloaking(cfg, f):
    """config.yaml cloaking_fix points at render mode commands (byte offsets in a joint's
    display list); the rebuilt display lists have them elsewhere: say where."""
    parts = (cfg.get('cloaking_fix') or {}).get('parts') or {}
    for obj, polys in parts.items():
        for poly, pc in (polys or {}).items():
            if not pc or 'part_offset' not in pc:
                continue
            # player struct joints[] start at 0x8E8 with TopN/TransN/XRotN/YRotN; DObjDesc i = joints[i + 4]
            joint = (int(str(pc['part_offset']), 16) - 0x8E8) // 4 - 4
            want = pc.get('cmd_offsets') or []
            want = [want] if isinstance(want, str) else want
            detail = 'hi' if poly == 'hipoly' else 'lo'
            dls = []
            d = f.details.get(detail)
            if d and joint < len(d['joints']) and d['joints'][joint].dl is not None:
                dls.append(('body', d['joints'][joint].dl))
            for v, pair in enumerate(f.modelparts.get(joint, [])):
                p = pair[0 if detail == 'hi' else 1]
                if p.get('dl') is not None:
                    dls.append((f'part {v}', p['dl']))
            found = {}
            for label, dl in dls:
                offs, o = [], dl
                while o + 8 <= len(f.char) and f.char[o] != 0xDF:
                    if MF.u32(f.char, o) == 0xE200001C:          # set render mode (not alpha compare)
                        offs.append(o - dl)
                    o += 8
                found.setdefault(tuple(offs), []).append(label)
            if found and not any(found):
                print(f'  cloaking_fix {obj}/{poly} (joint {joint}): its display lists set no render mode now, '
                      'so the cloaking device works without a fix: REMOVE this entry from config.yaml '
                      '(the fix would overwrite a command with an end of display list)')
                continue
            ok = all(set(int(str(x), 16) for x in want) <= set(k) for k in found)
            if not ok:
                where = '; '.join(f"{', '.join(v)}: {', '.join(f'0x{x:X}' for x in k) or 'none'}"
                                  for k, v in found.items())
                print(f'  cloaking_fix {obj}/{poly} (joint {joint}): cmd_offsets {want} are not render mode '
                      f'commands in the new display lists; they are at: {where}')


if __name__ == '__main__':
    main()
