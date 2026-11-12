#!/usr/bin/env python3
"""Export a fighter's model (main.bin + character.bin) for editing in Blender.

    python3 export_fighter.py CHAR_DIR OUT_DIR [--costumes N]
    python3 export_fighter.py --main main.bin --char character.bin OUT_DIR

CHAR_DIR is an extra_characters/<Name> folder (main.bin, character.bin,
config.yaml: pointer chain offsets and num_costumes are read from it).

Writes to OUT_DIR:
  model.gltf/.bin   hi + lo body (one mesh per joint and detail, rigid on the
                    joint's bone) and every alternate part (model parts)
  textures/         <material>_c<costume>.png for every costume palette,
                    <material>_expr<k>.png for facial expression frames
  manifest.json     what's what (joints, materials per costume, parts,
                    expressions, source offsets)
Then build the .blend with build_blend.py (run inside Blender).
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

import numpy as np
from PIL import Image

APPENDER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, APPENDER_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fighter_files as FF  # noqa: E402
from SSB import N64, SSBtbl  # noqa: E402

FLOAT, USHORT, UINT = 5126, 5123, 5125

VANILLA_FIGHTER_FILES = {
    'MARIO': (0xCB, 0x128),
    'FOX': (0xD1, 0x139),
    'DK': (0xD5, 0x13D),
    'SAMUS': (0xD9, 0x140),
    'LUIGI': (0xDD, 0x143),
    'LINK': (0xE1, 0x144),
    'YOSHI': (0xF7, 0x152),
    'CAPTAIN': (0xEC, 0x14C),
    'KIRBY': (0xE5, 0x148),
    'PIKACHU': (0xF3, 0x155),
    'JIGGLYPUFF': (0xE9, 0x14A),
    'NESS': (0xEF, 0x14F),
    'BOSS': (0xFA, 0x158),
    'METAL': (0xCE, 0x12C),
}
VANILLA_FIGHTER_COSTUMES = {
    **dict.fromkeys(('MARIO', 'FOX', 'DK', 'SAMUS', 'LUIGI', 'LINK', 'YOSHI',
                     'CAPTAIN', 'KIRBY', 'PIKACHU', 'JIGGLYPUFF', 'NESS'), 4),
    'BOSS': 1,
    'METAL': 1,
}
NEAREST, REPEAT, MIRRORED_REPEAT, CLAMP_TO_EDGE = 9728, 10497, 33648, 33071

def remix_fighter_files():
    """ROM file IDs for Remix fighters with both main and character files."""
    file_asm = os.path.join(APPENDER_DIR, 'smashremix', 'src', 'File.asm')
    if not os.path.isfile(file_asm):
        return {}
    pattern = re.compile(
        r'^\s*constant\s+([A-Z0-9_]+)_(MAIN|CHARACTER)\((0x[0-9A-Fa-f]+|\d+)\)'
    )
    parts = {}
    with open(file_asm, encoding='utf-8') as source:
        for line in source:
            match = pattern.match(line)
            if match:
                name, kind, value = match.groups()
                parts.setdefault(name, {})[kind] = int(value, 0)
    return {
        name: (files['MAIN'], files['CHARACTER'])
        for name, files in parts.items()
        if 'MAIN' in files and 'CHARACTER' in files
    }


REMIX_FIGHTER_FILES = remix_fighter_files()
FIGHTER_FILES = VANILLA_FIGHTER_FILES | REMIX_FIGHTER_FILES


def quat_from_matrix(R):
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        return [(R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s]
    i = int(np.argmax(np.diag(R)))
    if i == 0:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        return [0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s]
    if i == 1:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        return [(R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s]
    s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
    return [(R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s]


def read_config(char_dir):
    cfg = {}
    path = os.path.join(char_dir, 'config.yaml')
    if os.path.exists(path):
        try:
            import yaml
            cfg = yaml.safe_load(open(path)) or {}
        except Exception:
            cfg = {}
    return cfg


COLOR_FIELDS = {'prim': 0x50, 'env': 0x58, 'blend': 0x5C, 'light1': 0x60, 'light2': 0x64}   # in MObjSub
COLOR_FLAG_OF = {'prim': 0x200, 'env': 0x400, 'blend': 0x800, 'light1': 0x1000, 'light2': 0x2000}
COLOR_FLAGS = 0x3E00


def wrap_mode(cm, extent, size):
    """(manifest wrap mode, clamp extent) for one N64 tile axis (cm bit 0 = mirror,
    bit 1 = clamp). The RDP clamps texel coordinates to the tile's extent first,
    then wraps / mirrors them with the mask (= the texture size). When the extent
    fits in the texture, the clamp is what shows: 'clamp'. Vanilla fighters shift
    the tile and make it wider than the texture (Mario's face: 64 wide, a 32-texel
    half mirrored): the mode is then the mask's ('mirror' / 'repeat') and the clamp
    extent (in texture widths, e.g. 2.0) is returned too (else None). A clamped
    tile smaller than its texture (Meta Knight's skeleton cheeks: 21 of 32 rows
    loaded) returns its extent too: it clamps there, not at the texture's edge."""
    if cm & 2 and extent <= size:
        return 'clamp', (extent / size if extent < size else None)
    return ('mirror' if cm & 1 else 'repeat'), (extent / size if cm & 2 else None)


GL_WRAP = {'repeat': REPEAT, 'mirror': MIRRORED_REPEAT, 'clamp': CLAMP_TO_EDGE}


class Exporter:
    def __init__(self, f, out_dir, costumes):
        self.f, self.out, self.costumes = f, out_dir, costumes
        os.makedirs(os.path.join(out_dir, 'textures'), exist_ok=True)
        self.gl = {'asset': {'version': '2.0', 'generator': 'smashremix-extra fighter_model/export_fighter.py'},
                   'scene': 0, 'scenes': [{'nodes': []}], 'nodes': [], 'meshes': [], 'materials': [],
                   'textures': [], 'images': [], 'samplers': [{'magFilter': NEAREST, 'minFilter': NEAREST,
                                                               'wrapS': REPEAT, 'wrapT': REPEAT}],
                   'skins': [], 'accessors': [], 'bufferViews': [], 'buffers': []}
        self.blob = bytearray()
        self.sampler_index = {(REPEAT, REPEAT): 0}
        self.manifest = dict(costumes=costumes, joints=[], materials={}, objects={}, model_parts=[],
                             expressions=[], attributes_offset=f.attr,
                             character_sha1=hashlib.sha1(bytes(f.char)).hexdigest())
        self.mat_index = {}
        self.mat_sig = {}
        self.mobj_name = {}
        self.expr_mobjs = set()

    # ---- glTF helpers
    def add(self, data, ctype, comps, target=None, minmax=False):
        data = np.ascontiguousarray(data)
        while len(self.blob) % 4:
            self.blob.append(0)
        view = {'buffer': 0, 'byteOffset': len(self.blob), 'byteLength': data.nbytes}
        if target:
            view['target'] = target
        self.blob.extend(data.tobytes())
        self.gl['bufferViews'].append(view)
        acc = {'bufferView': len(self.gl['bufferViews']) - 1, 'componentType': ctype,
               'count': len(data), 'type': comps}
        if minmax:
            acc['min'], acc['max'] = data.min(0).tolist(), data.max(0).tolist()
        self.gl['accessors'].append(acc)
        return len(self.gl['accessors']) - 1

    # ---- skeleton
    def skeleton(self, joints):
        self.joint_node = {}
        for j in joints:
            self.joint_node[j.index] = len(self.gl['nodes'])
            R = FF.rot_zyx(*j.r)
            self.gl['nodes'].append({'name': f'joint_{j.index:02d}', 'translation': [float(x) for x in j.t],
                                     'rotation': [float(x) for x in quat_from_matrix(R)],
                                     'scale': [float(x) for x in j.s]})
            self.manifest['joints'].append(dict(index=j.index, parent=j.parent, depth=j.depth))
        for j in joints:
            kids = [self.joint_node[c.index] for c in joints if c.parent == j.index]
            if kids:
                self.gl['nodes'][self.joint_node[j.index]]['children'] = kids
        roots = [self.joint_node[j.index] for j in joints if j.parent is None]
        self.gl['scenes'][0]['nodes'] += roots
        ibm = np.array([np.linalg.inv(j.world).T.reshape(-1) for j in joints], '<f4')
        self.gl['skins'].append({'name': 'fighter', 'skeleton': roots[0],
                                 'joints': [self.joint_node[j.index] for j in joints],
                                 'inverseBindMatrices': self.add(ibm, FLOAT, 'MAT4')})
        self.joints = joints

    # ---- materials
    FMT = {(0, 2): 'RGBA16', (0, 3): 'RGBA32', (2, 0): 'CI4', (2, 1): 'CI8', (3, 0): 'IA4', (3, 1): 'IA8',
           (3, 2): 'IA16', (4, 0): 'I4', (4, 1): 'I8'}

    @staticmethod
    def wrap_entry(wrap, extent, size):
        """Manifest 'wrap': per axis mode, masks, and clamp_s / clamp_t (the tile's
        clamp extent in texture widths) when it clamps past the texture's size."""
        out = {}
        for i, a in enumerate('st'):
            mode, clamp = wrap_mode(wrap[i], extent[i], size[i])
            out[a] = mode
            if clamp is not None:
                out[f'clamp_{a}'] = clamp
        return dict(s=out['s'], t=out['t'], mask_s=wrap[2], mask_t=wrap[3],
                    **{k: v for k, v in out.items() if k.startswith('clamp_')})

    def sampler(self, entry):
        """glTF sampler index matching the material's wrap modes."""
        wrap = entry.get('wrap')
        if not wrap:
            return 0
        key = (GL_WRAP[wrap['s']], GL_WRAP[wrap['t']])
        if key not in self.sampler_index:
            self.sampler_index[key] = len(self.gl['samplers'])
            self.gl['samplers'].append({'magFilter': NEAREST, 'minFilter': NEAREST,
                                        'wrapS': key[0], 'wrapT': key[1]})
        return self.sampler_index[key]

    def material(self, key, desc, owner):
        i = self._material(key, desc)
        if key[0] == 'mobj':
            self.mobj_name.setdefault(key[1], self.gl['materials'][i]['name'])
        return i

    def costume_colors(self, mobj):
        """Per costume, the MObj colors it draws with (those its flags enable): the MObjSub's,
        changed by its costume script's color events. Hex RRGGBBAA."""
        f = self.f
        flags = FF.u16(f.char, mobj + 0x30)
        base = {k: FF.u32(f.char, mobj + off) for k, off in COLOR_FIELDS.items()}
        on = [k for k, bit in COLOR_FLAG_OF.items() if flags & bit]
        sc = self.script_of.get(mobj)
        frames = FF.decode_script(f.char, sc) if sc else []
        out = []
        for c in range(self.costumes):
            fr = frames[min(c, len(frames) - 1)] if frames else {}
            out.append({k: f'{fr.get(k, base[k]):08X}' for k in on})
        return out

    def real_entries(self, addrs, size, name, what):
        """An image / palette list stops at its first entry that can't be one: data that runs
        into something else (another pointer target inside it). Lists have no stored length,
        and some carry leftover pointers to unrelated data after the real entries."""
        if not hasattr(self, '_targets'):
            self._targets = np.array(sorted(set(self.f.char_int.values())))
        for i, a in enumerate(addrs):
            if a is None:
                continue
            k = np.searchsorted(self._targets, a, side='right')
            if k < len(self._targets) and self._targets[k] < a + size:
                print(f'  note: {name}: {what} list cut at entry {i} of {len(addrs)} (0x{a:X} overlaps other data)')
                return addrs[:max(i, 1)]
        return addrs

    def model_light(self, cmd):
        """Is this a light color command that just repeats the model's own light colors?"""
        w0, w1 = cmd
        lights = self.manifest.get('lights') or {}
        if w0 in (0xDB0A0000, 0xDB0A0004):
            return f'{w1 >> 8:06X}' == lights.get('light')
        if w0 in (0xDB0A0018, 0xDB0A001C):
            return f'{w1 >> 8:06X}' == lights.get('shadow')
        return False

    def mobj_order(self, mobjs):
        """Material names in a joint's MObj list order (None: an MObj its DL never uses).
        The order matters: texture parts (facial expressions) and code address MObjs by index."""
        return [self.mobj_name.get(ms) for ms in mobjs]

    def _material(self, key, desc):
        """One material: every image / palette list entry as a PNG, plus one PNG per
        costume (the entries its costume script selects), and how it's drawn."""
        if key in self.mat_index:
            return self.mat_index[key]
        name = f'mat_{len(self.manifest["materials"]):03d}'
        f = self.f
        entry = dict(kind=key[0], images=[], palettes=[], costumes=[], costume_map=[])
        if key[0] == 'prim':                     # untextured, one color (prim), no MObj
            entry['color'] = list(key[1:5])
            entry['state'] = [[f'{v[0]:08X}', f'{v[1]:08X}'] for k, v in key[5] if not self.model_light(v)]
        elif key[0] == 'mobj' and key[2] is None:
            # untextured, colored by its MObj (prim / env / light colors, may change per costume)
            mobj = key[1]
            entry.update(untextured=True, mobj=mobj, flags=FF.u16(f.char, mobj + 0x30),
                         state=[[f'{v[0]:08X}', f'{v[1]:08X}'] for k, v in key[7] if not self.model_light(v)],
                         costume_map=[[0, 0]] * self.costumes)
            entry['costume_colors'] = self.costume_colors(mobj)
            c0 = entry['costume_colors'][0].get('prim') if entry['costume_colors'] else None
            prim = int(c0, 16) if c0 else dict(key[7]).get((0xFA, 0), (0, 0xFFFFFFFF))[1]
            entry['color'] = [(prim >> sh) & 255 for sh in (24, 16, 8, 0)]
            ms = bytearray(f.char[mobj:mobj + 0x78])
            ms[0x04:0x08] = ms[0x2C:0x30] = b'\0' * 4
            sig = ('untextured', json.dumps(entry['state']), json.dumps(entry['costume_colors']), bytes(ms))
            if sig in self.mat_sig:
                self.mat_index[key] = self.mat_sig[sig]
                return self.mat_index[key]
        else:
            tile_w, tile_h = desc['size']
            wrap, state = (key[6], key[7]) if key[0] == 'mobj' else (key[5], key[6])
            w, h = FF.texture_dimensions(desc['size'], wrap)
            fmt, siz = desc['tile']
            entry.update(size=[w, h], tile_size=[tile_w, tile_h], fmt=fmt, siz=siz,
                         format=self.FMT.get((fmt, siz), f'{fmt}/{siz}'),
                         wrap=self.wrap_entry(wrap, (tile_w, tile_h), (w, h)),
                         state=[[f'{v[0]:08X}', f'{v[1]:08X}']
                                for k, v in state if not self.model_light(v)])
            if key[0] == 'mobj':
                mobj = key[1]
                info = FF.mobj_textures(f, mobj, (w, h))
                flags = info['flags']
                imgs = self.real_entries(info['images'] if flags & 0x11 else [], FF.row_bytes(w, siz) * h, name, 'image')
                # palette size: what's loaded (the DL's TLUT load, or the MObj's: 256 if sub.siz is 8b)
                n_cols = desc.get('tlut_n') or (256 if f.char[mobj + 3] == 1 else 16)
                entry['palette_colors'] = n_cols
                pals = self.real_entries(info['palettes'] if flags & 0x04 else [], 2 * n_cols, name, 'palette')
                entry.update(mobj=mobj, flags=flags, mobj_fmt=f.char[mobj + 2], mobj_siz=f.char[mobj + 3])
                if flags & COLOR_FLAGS:
                    entry['costume_colors'] = self.costume_colors(mobj)
                if not imgs:
                    imgs = [key[4]]
                    entry['image_source'] = 'inline'
                else:
                    entry['image_source'] = 'list'
                if not pals and fmt == 2:
                    pals = [key[5]]
                    entry['palette_source'] = 'inline'
                else:
                    entry['palette_source'] = 'list' if pals else None
                frames = FF.decode_script(f.char, self.script_of.get(mobj)) if self.script_of.get(mobj) else []
            else:
                imgs, pals = [key[1]], ([key[2]] if fmt == 2 else [])
                if fmt == 2 and desc.get('tlut_n'):
                    entry['palette_colors'] = desc['tlut_n']
                entry.update(image_source='inline', palette_source='inline' if pals else None)
                frames = []
            cmap = []
            for c in range(self.costumes):
                fr = frames[c] if c < len(frames) else {}
                # an index the script never sets stays 0 (the MObj's initial value)
                ti = min(fr.get('tex', 0), len(imgs) - 1)
                pi = min(fr.get('pal', 0), max(len(pals) - 1, 0))
                cmap.append([ti, pi])
            entry['costume_map'] = cmap
            entry['image_addrs'] = [int(x) if x is not None else None for x in imgs]
            entry['palette_addrs'] = [int(x) if x is not None else None for x in pals]
            p0 = pals[cmap[0][1]] if pals else None
            i0 = imgs[cmap[0][0]]
            # same content as a material already exported (e.g. the hi and lo models' copies of one
            # MObjSub, often with their own copy of the same textures): one material, so an
            # edit applies to every model using it
            ms = bytearray(f.char[entry['mobj']:entry['mobj'] + 0x78]) if key[0] == 'mobj' else bytearray()
            if ms:
                ms[0x04:0x08] = ms[0x2C:0x30] = b'\0' * 4
            nb, npb = w * h * (4, 8, 16, 32)[siz] // 8, 2 * (256 if siz == 1 else 16)
            sig = (key[0], w, h, tile_w, tile_h, fmt, siz, json.dumps(entry['wrap']), json.dumps(entry['state']),
                   entry.get('image_source'), entry.get('palette_source'),
                   tuple(bytes(f.char[a:a + nb]) if a is not None else None for a in imgs),
                   tuple(bytes(f.char[a:a + npb]) if a is not None else None for a in pals),
                   json.dumps(cmap), bytes(ms), json.dumps(entry.get('costume_colors')))
            if sig in self.mat_sig:
                self.mat_index[key] = self.mat_sig[sig]
                return self.mat_index[key]
            entry['images'] = [self.save_tex(name, f'img{i}', im, fmt, siz, w, h, p0) for i, im in enumerate(imgs)]
            entry['palettes'] = [self.save_tex(name, f'pal{i}', i0, fmt, siz, w, h, pl) for i, pl in enumerate(pals)]
            # costume images (what Blender shows): past a clamped tile's extent, the edge
            # texel (the game never draws what's there). The img / pal PNGs keep it all.
            entry['costumes'] = [self.save_tex(name, f'c{c}', imgs[ti], fmt, siz, w, h, pals[pi] if pals else None,
                                               fill=(entry['wrap'], (tile_w, tile_h)))
                                 for c, (ti, pi) in enumerate(cmap)]
        self.manifest['materials'][name] = entry
        m = {'name': name, 'pbrMetallicRoughness': {'metallicFactor': 0.0, 'roughnessFactor': 1.0},
             'doubleSided': True}
        if entry['costumes'] and entry['costumes'][0]:
            self.gl['images'].append({'uri': entry['costumes'][0]})
            self.gl['textures'].append({'source': len(self.gl['images']) - 1,
                                        'sampler': self.sampler(entry)})
            m['pbrMetallicRoughness']['baseColorTexture'] = {'index': len(self.gl['textures']) - 1}
            m['alphaMode'] = 'MASK'
        else:
            c = np.array(entry.get('color', [200, 200, 200, 255])[:3]) / 255
            lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
            m['pbrMetallicRoughness']['baseColorFactor'] = [*map(float, lin), 1.0]
        self.gl['materials'].append(m)
        self.mat_index[key] = len(self.gl['materials']) - 1
        if entry['kind'] != 'prim':
            self.mat_sig[sig] = self.mat_index[key]
        return self.mat_index[key]

    def save_tex(self, name, suffix, img, fmt, siz, w, h, pal, fill=None):
        if img is None:
            return None
        rgba = FF.decode_texture(self.f.char, img, fmt, siz, w, h, pal)
        if fill:
            rgba = FF.clamp_fill(rgba, *fill)
        rel = f'textures/{name}_{suffix}.png'
        Image.fromarray(rgba, 'RGBA').save(os.path.join(self.out, rel))
        return rel

    # ---- meshes
    def mesh(self, name, geo, owner_of=None, extra=None):
        """One glTF mesh per (joint drawing it): vertices rigid on their source joint."""
        by_joint = {}
        for t in geo.tris:
            by_joint.setdefault(t.joint, []).append(t)
        for j, tris in sorted(by_joint.items()):
            pos, nrm, uv, jnt, col, prims = [], [], [], [], [], {}
            for t in tris:
                desc = geo.materials[t.material]
                mi = self.material(t.material, desc, None)
                wrap = desc['wrap']
                w, h = FF.texture_dimensions(desc['size'], wrap)
                lit = desc['state'].get((0xD9, 0), (0, FF.GEOM_DEFAULT))[1] & FF.G_LIGHTING
                idx = []
                world = [self.joints[c[0]].world @ np.array([*c[1], 1.0]) for c in t.corners]
                face_n = np.cross(world[1][:3] - world[0][:3], world[2][:3] - world[0][:3])
                face_n = face_n / (np.linalg.norm(face_n) or 1)
                for (src, p, n, st, rgba), wp in zip(t.corners, world):
                    W = self.joints[src].world
                    pos.append(wp[:3])
                    if lit:          # vertex bytes are a normal
                        nn = W[:3, :3] @ np.array(n, float)
                        nrm.append(nn / (np.linalg.norm(nn) or 1))
                        col.append((1.0, 1.0, 1.0, 1.0))
                    else:            # unlit: they're the vertex color (RGBA)
                        nrm.append(face_n)
                        # glTF colors are linear; Blender stores them as sRGB bytes: written as
                        # sRGB -> linear, Blender holds (and shows) the game's exact byte values
                        col.append(tuple(FF.srgb_to_linear(x / 255) for x in rgba[:3]) + (rgba[3] / 255,))
                    uv.append(((st[0] - t.tile_ul[0]) / w, (st[1] - t.tile_ul[1]) / h))
                    jnt.append(src)
                    idx.append(len(pos) - 1)
                prims.setdefault(mi, []).extend(idx)
            P = np.array(pos, '<f4')
            J = np.zeros((len(jnt), 4), '<u2')
            J[:, 0] = jnt
            Wt = np.zeros((len(jnt), 4), '<f4')
            Wt[:, 0] = 1
            attrs = {'POSITION': self.add(P, FLOAT, 'VEC3', 34962, True),
                     'NORMAL': self.add(np.array(nrm, '<f4'), FLOAT, 'VEC3', 34962),
                     'TEXCOORD_0': self.add(np.array(uv, '<f4'), FLOAT, 'VEC2', 34962),
                     'COLOR_0': self.add(np.array(col, '<f4'), FLOAT, 'VEC4', 34962),
                     'JOINTS_0': self.add(J, USHORT, 'VEC4', 34962),
                     'WEIGHTS_0': self.add(Wt, FLOAT, 'VEC4', 34962)}
            mesh_name = f'{name}_j{j:02d}'
            self.gl['meshes'].append({'name': mesh_name, 'primitives': [
                {'attributes': attrs, 'material': mi,
                 'indices': self.add(np.array(ix, '<u4'), UINT, 'SCALAR', 34963)} for mi, ix in prims.items()]})
            self.gl['nodes'].append({'name': mesh_name, 'mesh': len(self.gl['meshes']) - 1, 'skin': 0})
            self.gl['scenes'][0]['nodes'].append(len(self.gl['nodes']) - 1)
            self.manifest['objects'][mesh_name] = dict(joint=j, triangles=len(tris), **(extra or {}))

    def run(self):
        f = self.f
        hi = f.details['hi']
        self.script_of = {}
        for d in f.details.values():
            if d.get('matanim') is None:
                continue
            for j, lst in d['mobjs'].items():
                an = f.char_int.get(d['matanim'] + 4 * j)
                for i, ms in enumerate(lst):
                    sc = f.char_int.get(an + 4 * i) if an is not None else None
                    if sc is not None:
                        self.script_of.setdefault(ms, sc)
        for joint, variants in f.modelparts.items():
            for pair in variants:
                for part in pair:
                    an = f.ptr_main(part['field'] + 8)
                    ms_list = FF.pointer_run(f.char_int, part['mobjsubs']) if part['mobjsubs'] is not None else []
                    if an and an[0] == 'char':
                        for i, ms in enumerate(ms_list):
                            sc = f.char_int.get(an[1] + 4 * i)
                            if sc is not None:
                                self.script_of.setdefault(ms, sc)
        self.skeleton(hi['joints'])
        self.manifest['lights'] = FF.model_lights(f)       # light / shadow (ambient) colors
        self.manifest['expressions'] = f.textureparts      # (texture-index switches: joint + material index)
        for detail in ('hi', 'lo'):
            d = f.details.get(detail)
            if not d:
                continue
            dls = [(j.index, j.dl) for j in d['joints']]
            geo, _ = FF.decode_model(f, d['joints'], d['mobjs'], dls)
            self.mesh(detail, geo, extra=dict(detail=detail))
            self.manifest.setdefault('mobj_order', {})[detail] = {
                str(j): self.mobj_order(lst) for j, lst in d['mobjs'].items() if lst}
        for joint, variants in sorted(f.modelparts.items()):
            for v, pair in enumerate(variants):
                for k, detail in enumerate(('hi', 'lo')):
                    part = pair[k]
                    if part['dl'] is None:
                        continue
                    lst = FF.pointer_run(f.char_int, part['mobjsubs']) if part['mobjsubs'] is not None else []
                    try:
                        geo, _ = FF.decode_model(f, self.joints, {joint: lst}, [(joint, part['dl'])])
                    except FF.BadDL as err:
                        print(f'  note: joint {joint} part {v} {detail}: {err}; not exported (kept as is)')
                        continue
                    name = f'part{v}_{detail}'
                    self.mesh(name, geo, extra=dict(detail=detail, model_part=v))
                    self.manifest['model_parts'].append(dict(joint=joint, variant=v, detail=detail,
                                                             field=part['field'], object=f'{name}_j{joint:02d}',
                                                             mobj_order=self.mobj_order(lst)))
        # electric-shock skeleton models (drawn instead of the body while shocked)
        hi_mobjs = f.details['hi']['mobjs']
        for sid, ents in sorted(f.skeletons.items()):
            for j, e in sorted(ents.items()):
                if e['flags'] & 0xF:
                    print(f'  note: skeleton {sid} joint {j}: two-DL entry, not exported (kept as is)')
                    continue
                try:
                    geo, _ = FF.decode_model(f, self.joints, {j: hi_mobjs.get(j, [])}, [(j, e['dl'])])
                except FF.BadDL as err:
                    print(f'  note: skeleton {sid} joint {j}: {err}; not exported (kept as is)')
                    continue
                name = f'skel{sid}'
                self.mesh(name, geo, extra=dict(detail='skeleton', skeleton=sid))
                # which of the joint's body MObjs (by index) each of its materials calls
                lst = hi_mobjs.get(j, [])
                slots = {self.gl['materials'][self.mat_index[k]]['name']: lst.index(k[1])
                         for k in geo.materials if k[0] == 'mobj' and k in self.mat_index and k[1] in lst}
                self.manifest.setdefault('skeletons', []).append(dict(id=sid, joint=j, field=e['field'],
                                                                      flags=e['flags'], object=f'{name}_j{j:02d}',
                                                                      mobj_slots=slots))
        self.gl['buffers'].append({'uri': 'model.bin', 'byteLength': len(self.blob)})
        open(os.path.join(self.out, 'model.bin'), 'wb').write(self.blob)
        json.dump(self.gl, open(os.path.join(self.out, 'model.gltf'), 'w'), indent=1)
        json.dump(self.manifest, open(os.path.join(self.out, 'manifest.json'), 'w'), indent=1, default=int)


def costumes_in_scripts(f):
    """Costume count from the costume scripts (one frame per costume), when config.yaml
    doesn't say: the longest script of the body's materials."""
    best = 0
    for d in f.details.values():
        if d.get('matanim') is None:
            continue
        for j, lst in d['mobjs'].items():
            an = f.char_int.get(d['matanim'] + 4 * j)
            for i in range(len(lst)):
                sc = f.char_int.get(an + 4 * i) if an is not None else None
                if sc is not None:
                    best = max(best, len(FF.decode_script(f.char, sc)))
    return best


def rom_external_data(entries, entry):
    """A ROM file's data, followed by the files its external relocation chain points
    into, and {field offset: target offset} for those pointers (into the appended
    copies). Vanilla electric-shock skeletons take their bone texture from a shared
    file this way. Each external pointer's file ID comes from the entry's ID list,
    in chain order."""
    data = bytearray(entry.extract('data'))
    if entry.res is None:
        return bytes(data), {}
    ids = [int.from_bytes(entry.idx[i:i + 2], 'big') for i in range(0, len(entry.idx or b''), 2)]
    links = list(FF.chain(data, entry.res).items())
    base, ext = {}, {}
    for (field_off, target), file_id in zip(links, ids):
        if not 0 <= file_id < len(entries):
            continue
        if file_id not in base:
            data.extend(bytes(-len(data) % 8))
            base[file_id] = len(data)
            data.extend(entries[file_id].extract('data'))
        ext[field_off] = base[file_id] + target
    return bytes(data), ext


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+', metavar='PATH',
                    help='OUT_DIR, or CHAR_DIR OUT_DIR')
    ap.add_argument('--main')
    ap.add_argument('--char')
    ap.add_argument('--rom', help='extract the source fighter files from an N64 ROM')
    ap.add_argument('--fighter', type=str.upper, choices=FIGHTER_FILES,
                    help='fighter name to extract from --rom (vanilla name or File.asm constant)')
    ap.add_argument('--main-file-id', type=lambda value: int(value, 16),
                    help='hex ROM file ID for main.bin (use with --char-file-id)')
    ap.add_argument('--char-file-id', type=lambda value: int(value, 16),
                    help='hex ROM file ID for character.bin (use with --main-file-id)')
    ap.add_argument('--costumes', type=int)
    ap.add_argument('--no-preview', action='store_true', help="don't render previews/*.png")
    a = ap.parse_args()
    if len(a.paths) == 1:
        char_dir, out_dir = None, a.paths[0]
    elif len(a.paths) == 2:
        char_dir, out_dir = a.paths
    else:
        ap.error('expected OUT_DIR or CHAR_DIR OUT_DIR')

    if a.rom and (a.main or a.char):
        ap.error('--rom cannot be combined with --main or --char')
    if not a.rom and (a.fighter or a.main_file_id is not None or a.char_file_id is not None):
        ap.error('--fighter and ROM file IDs require --rom')

    cfg = read_config(char_dir) if char_dir else {}
    off = cfg.get('offsets', {})
    mi, me = (int(x, 16) for x in off.get('main', ['02CC', '0000']))
    ci = int(off.get('character', ['0004'])[0], 16)
    if a.rom:
        if a.fighter:
            if a.main_file_id is not None or a.char_file_id is not None:
                ap.error('--fighter cannot be combined with explicit ROM file IDs')
            main_id, char_id = FIGHTER_FILES[a.fighter]
        elif a.main_file_id is None or a.char_file_id is None:
            ap.error('--rom requires --fighter or both --main-file-id and --char-file-id')
        else:
            main_id, char_id = a.main_file_id, a.char_file_id

        entries = SSBtbl.fromROM(N64(open(a.rom, 'rb').read()))
        if not (0 <= main_id < len(entries) and 0 <= char_id < len(entries)):
            ap.error(f'ROM file ID out of range (ROM has {len(entries)} entries)')
        main_entry, char_entry = entries[main_id], entries[char_id]
        char_data, char_ext = rom_external_data(entries, char_entry)
        with tempfile.TemporaryDirectory() as temp_dir:
            main_path = os.path.join(temp_dir, 'main.bin')
            char_path = os.path.join(temp_dir, 'character.bin')
            with open(main_path, 'wb') as main_file:
                main_file.write(main_entry.extract('data'))
            with open(char_path, 'wb') as char_file:
                char_file.write(char_data)
            f = FF.load(main_path, char_path, main_entry.tbl or 0,
                        main_entry.res or 0, char_entry.tbl or 0, char_ext)
    else:
        main_path = a.main or os.path.join(char_dir, 'main.bin')
        char_path = a.char or os.path.join(char_dir, 'character.bin')
        f = FF.load(main_path, char_path, mi, me, ci)

    tf = (cfg.get('attributes') or {}).get('texture_forms') if not a.rom else None
    if tf:   # texture parts the build writes into main.bin (config.yaml attributes.texture_forms)
        seen = {}
        f.textureparts = []
        for k, part in enumerate(tf):
            gj = int(str(part), 16)
            occ = seen.get(gj, 0)
            seen[gj] = occ + 1
            f.textureparts.append(dict(index=k, joint=gj - FF.JOINT_COMMON_START, mobj_hi=occ, mobj_lo=occ,
                                       source='config.yaml texture_forms'))
    n = (a.costumes or cfg.get('num_costumes')
         or (VANILLA_FIGHTER_COSTUMES.get(a.fighter, 4) if a.fighter else None)
         or (1 if a.rom else costumes_in_scripts(f)) or 1)
    ex = Exporter(f, out_dir, n)
    ex.run()
    m = ex.manifest
    tris = sum(o['triangles'] for o in m['objects'].values())
    print(f"exported {len(m['joints'])} joints, {len(m['objects'])} meshes ({tris} triangles), "
          f"{len(m['materials'])} materials x {n} costumes, {len(m['model_parts'])} model parts, "
          f"{len(m['expressions'])} expression slots, "
          f"{sum(len(v['images']) for v in m['materials'].values())} image + "
          f"{sum(len(v['palettes']) for v in m['materials'].values())} palette entries -> {out_dir}")
    if not a.no_preview:
        import preview
        for p in preview.save_sheets(out_dir):
            print(f'preview: {p}')


if __name__ == '__main__':
    main()
