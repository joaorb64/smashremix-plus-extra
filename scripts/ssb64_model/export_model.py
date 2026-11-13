#!/usr/bin/env python3
"""Export a Smash 64 model (fighter, item / projectile, stage) for editing in Blender.
Usually run through ssb64_model.py export, which picks the options and builds the .blend.

    python3 export_model.py CHAR_DIR OUT_DIR [--costumes N]          fighter
    python3 export_model.py --main main.bin --char character.bin OUT_DIR
    python3 export_model.py FOLDER OUT_DIR --object NAME             item / projectile
    python3 export_model.py STAGE_DIR OUT_DIR --stage                stage
    python3 export_model.py --rom ROM.z64 --fighter X | --stage X | --item X OUT_DIR

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
import struct
import sys
import tempfile

import numpy as np
from PIL import Image

APPENDER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, APPENDER_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_files as MF  # noqa: E402
import matanim as MA  # noqa: E402
import jointanim as JA  # noqa: E402
import gamedata as GD  # noqa: E402
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
# vanilla common items: ITAttributes offsets in ITCommonData (file 0xFB), from the decomp's
# tools/relocFileDescriptions.us.txt
ITCOMMON_FILE = 0xFB
VANILLA_ITEMS = {
    'CAPSULE': 0x50, 'TOMATO': 0xB8, 'HEART': 0x100, 'STAR': 0x148, 'SWORD': 0x190, 'BAT': 0x1D8,
    'HARISEN': 0x220, 'LGUN': 0x268, 'FFLOWER': 0x2E4, 'HAMMER': 0x374, 'MSBOMB': 0x3BC,
    'BOMBHEI': 0x424, 'STARROD': 0x48C, 'GSHELL': 0x53C, 'RSHELL': 0x584, 'BOX': 0x5CC, 'TARU': 0x634,
    'NBUMPER': 0x69C, 'MBALL': 0x6E4, 'EGG': 0xACC,
}
# vanilla stages: their header file (decomp: Map file; MPGroundData at 0x14), decomp tools/relocFileDescriptions.us.txt
VANILLA_STAGES = {
    'DREAMLAND': 255, 'BETA_DREAMLAND': 256, 'ZEBES': 257, 'TEST': 258, 'CASTLE': 259,
    'MUSHROOM_KINGDOM': 260, 'JUNGLE': 261, 'SECTOR_Z': 262, 'YOSHIS_ISLAND': 263, 'SAFFRON': 264,
    'HYRULE': 265, 'FINAL_DESTINATION': 266, 'HOW_TO_PLAY': 267, 'DUEL_ZONE': 268, 'META_CRYSTAL': 269,
    'SMALL_YOSHIS_ISLAND': 270,
}


def remix_stages():
    """Remix stages' header (Map) files: File.asm *_HEADER constants."""
    file_asm = os.path.join(APPENDER_DIR, 'smashremix', 'src', 'File.asm')
    out = {}
    if os.path.isfile(file_asm):
        for line in open(file_asm, encoding='utf-8'):
            m = re.match(r'^\s*constant\s+(?:STG_)?([A-Z0-9_]+)_HEADER\((0x[0-9A-Fa-f]+|\d+)\)', line)
            if m:
                out[m.group(1)] = int(m.group(2), 0)
    return out


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


def object_files(cfg, char_dir, name):
    """An extra character's item / projectile by name (e.g. 'knuckle'): its attributes file
    (config.yaml files: <name>_hitbox or <name>) and model file (append_files: <name>_gfx),
    with their chain heads -> dict for MF.load_object."""
    def entries(lst):
        for e in lst or []:
            if e and isinstance(e[0], list):
                yield from entries(e)
            elif e:
                yield e
    files = {e[0]: e for e in entries(cfg.get('files'))}
    extra = {e[0]: e for e in entries(cfg.get('append_files'))}
    attr = files.get(f'{name}_hitbox') or files.get(name)
    if attr is None:
        raise SystemExit(f"config.yaml files: has no {name}_hitbox / {name} entry")
    model = (extra.get(f'{name}_gfx') or files.get(f'{name}_gfx')
             or (files.get(name) if attr[0] != name else None))       # stage items: NAME + NAME_hitbox
    out = dict(attr_path=os.path.join(char_dir, attr[0] + '.bin'),
               attr_int_head=int(attr[1], 16), attr_ext_head=int(attr[2], 16))
    # the attributes start with their data pointer, the first link of the external chain
    # (Remix projectiles: 0; stage items: a footer at 0x40)
    if out['attr_ext_head'] < 0x3FFFC:
        out['attr_offset'] = out['attr_ext_head']
    if model is not None:
        out.update(model_path=os.path.join(char_dir, model[0] + '.bin'), model_int_head=int(model[1], 16))
    return out


def stage_files(cfg, stage_dir):
    """A stage folder's header.bin / stage.bin and chain heads (config.yaml offsets)."""
    off = cfg.get('offsets') or {}
    if 'header' not in off or 'stage' not in off:
        raise SystemExit(f'{stage_dir}/config.yaml: offsets: header / stage missing')
    return dict(header_path=os.path.join(stage_dir, 'header.bin'), stage_path=os.path.join(stage_dir, 'stage.bin'),
                header_int_head=int(off['header'][0], 16), header_ext_head=int(off['header'][1], 16),
                stage_int_head=int(off['stage'][0], 16))


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
        self.gl = {'asset': {'version': '2.0', 'generator': 'smashremix-extra ssb64_model/export_model.py'},
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
    def skeleton(self, joints, prefix='', name='fighter'):
        """One skin (armature) of joints named <prefix>joint_NN; meshes made next use it."""
        self.joint_node = {}
        for j in joints:
            self.joint_node[j.index] = len(self.gl['nodes'])
            R = MF.rot_zyx(*j.r)
            self.gl['nodes'].append({'name': f'{prefix}joint_{j.index:02d}', 'translation': [float(x) for x in j.t],
                                     'rotation': [float(x) for x in quat_from_matrix(R)],
                                     'scale': [float(x) for x in j.s]})
            self.manifest['joints'].append(dict(index=j.index, parent=j.parent, depth=j.depth,
                                                **({'prefix': prefix} if prefix else {})))
        for j in joints:
            kids = [self.joint_node[c.index] for c in joints if c.parent == j.index]
            if kids:
                self.gl['nodes'][self.joint_node[j.index]]['children'] = kids
        roots = [self.joint_node[j.index] for j in joints if j.parent is None]
        self.gl['scenes'][0]['nodes'] += roots
        ibm = np.array([np.linalg.inv(j.world).T.reshape(-1) for j in joints], '<f4')
        self.skin = len(self.gl['skins'])
        self.gl['skins'].append({'name': name, 'skeleton': roots[0],
                                 'joints': [self.joint_node[j.index] for j in joints],
                                 'inverseBindMatrices': self.add(ibm, FLOAT, 'MAT4')})
        self.joints = joints

    def animation(self, name, anims):
        """A glTF animation sampling joint animations every frame (60 per second): the
        joint's rest transform with its animated tracks (rotation x/y/z, translation,
        scale) replaced, as the game plays them (the scripts themselves are kept by the
        import, unchanged). Each joint has its own length (a looping one: one cycle)."""
        times_of = {}
        samplers, channels = [], []
        for node, j, seq in anims:
            n = len(seq)
            if n not in times_of:
                times_of[n] = self.add(np.arange(n, dtype='<f4')[:, None] / 60.0, FLOAT, 'SCALAR', None, True)
            times = times_of[n]
            T, R, S = [], [], []
            r, t, sc = list(j.r), list(j.t), list(j.s)
            for v in seq:
                for k in range(3):
                    r[k] = v.get(k, r[k])
                    t[k] = v.get(4 + k, t[k])
                    sc[k] = v.get(7 + k, sc[k])
                T.append(list(t))
                R.append(quat_from_matrix(MF.rot_zyx(*r)))
                S.append(list(sc))
            rest = {'translation': list(j.t), 'rotation': quat_from_matrix(MF.rot_zyx(*j.r)), 'scale': list(j.s)}
            for path, vals, typ in (('translation', T, 'VEC3'), ('rotation', R, 'VEC4'), ('scale', S, 'VEC3')):
                arr = np.array(vals, '<f4')
                if np.allclose(arr, arr[0]) and np.allclose(arr[0], np.array(rest[path], '<f4')):
                    continue                              # not animated (it stays at the rest pose)
                samplers.append({'input': times, 'output': self.add(arr, FLOAT, typ), 'interpolation': 'LINEAR'})
                channels.append({'sampler': len(samplers) - 1, 'target': {'node': node, 'path': path}})
        if channels:
            self.gl.setdefault('animations', []).append({'name': name, 'samplers': samplers, 'channels': channels})

    def joint_animations(self, name, d, anims):
        """Joint animations (anim_joints scripts; stage layers, items, projectiles): each joint's
        values per frame (-> anims, for the glTF), its length and loop (-> manifest). Blender
        edits them; blend_to_gltf.py samples the bones back, the import writes the scripts (the
        original ones where nothing changed). -> the longest one's frame count."""
        f = self.f
        info = {}
        for j in d['joints']:
            sc = f.char_int.get(d['anim'] + 4 * j.index) if d.get('anim') is not None else None
            if sc is None:
                continue
            a = JA.play(f.char, f.char_int, sc, j)
            info[j.index] = dict(frames=a['frames'], loop=a['loop'], loop_start=a['loop_start'],
                                 tracks=a['tracks'], locked=JA.uses_interp(f.char, f.char_int, sc))
            idx = {k: JA.NAMES.index(k) for k in JA.EDITABLE}
            seq = [{idx[k]: a['samples'][k][n] for k in JA.EDITABLE} for n in range(a['frames'] + 1)]
            anims.append((self.joint_node[j.index], j, seq))
        if not info:
            return 0
        frames = max(v['frames'] for v in info.values()) + 1
        self.manifest.setdefault('animations', {})[name] = dict(
            joints=sorted(info), frames=frames, field=d['field'] + 4,
            cycles={str(j): v['frames'] for j, v in info.items() if v['loop']},
            joint_info={str(j): v for j, v in info.items()})
        return frames

    def run_stage(self):
        """Stage geometry: one armature + meshes per layer (L<n>_joint_NN, L<n>_j<joint>)."""
        f = self.f
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
        self.manifest['lights'] = MF.model_lights(f)
        self.manifest['expressions'] = []
        # MPGroundData: light_angle (x, y degrees: the scene light's direction, which every lit
        # model is shaded with; z: the camera's tilt, radians) and the camera bounds
        g = f.object['ground']
        if g + 0x74 <= len(f.main):
            self.manifest['stage_view'] = dict(
                light_angle=[round(v, 6) for v in struct.unpack_from('>3f', f.main, g + 0x60)],
                camera_bounds=list(struct.unpack_from('>4h', f.main, g + 0x6C)))     # top, bottom, right, left
        anims = []                                    # (joint node, rest joint, [{track: value}])
        n_frames = 0
        for name, d in f.details.items():
            L = int(name[1:])
            self.skeleton(d['joints'], prefix=f'{name}_', name=f'Layer {L}')
            n_frames = max(n_frames, self.joint_animations(name, d, anims))
            geo, _ = MF.decode_model(f, d['joints'], d['mobjs'], [(j.index, j.dl) for j in d['joints']],
                                     carry_state=True)
            self.mesh(name, geo, extra=dict(detail=name, layer=L), preserve_order=True)
            self.manifest.setdefault('mobj_order', {})[name] = {
                str(j): self.mobj_order(lst) for j, lst in d['mobjs'].items() if lst}
        if anims:
            self.animation('Stage', anims)
        self.gl['buffers'].append({'uri': 'model.bin', 'byteLength': len(self.blob)})
        open(os.path.join(self.out, 'model.bin'), 'wb').write(self.blob)
        json.dump(self.gl, open(os.path.join(self.out, 'model.gltf'), 'w'), indent=1)
        json.dump(self.manifest, open(os.path.join(self.out, 'manifest.json'), 'w'), indent=1, default=int)

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
        flags = MF.u16(f.char, mobj + 0x30)
        base = {k: MF.u32(f.char, mobj + off) for k, off in COLOR_FIELDS.items()}
        on = [k for k, bit in COLOR_FLAG_OF.items() if flags & bit]
        sc = self.script_of.get(mobj)
        frames = MF.decode_script(f.char, sc) if sc else []
        out = []
        for c in range(self.costumes):
            fr = frames[min(c, len(frames) - 1)] if frames else {}
            out.append({k: f'{fr.get(k, base[k]):08X}' for k in on})
        return out

    def material_scripts(self):
        """Do the MObj scripts animate materials (stages, items, projectiles)? A fighter's
        are its costume scripts (a frame per costume)."""
        return bool(getattr(self.f, 'object', None))

    def mobj_anim(self, entry, mobj):
        """Stages / items: the MObj's starting values (offsets, scales, LOD fraction) and tile
        setup, and its material script (texture flips, scrolling, palette / color changes)
        played into keyframes (matanim.py) for Blender. The import keeps the script as it
        is unless the keyframes were edited."""
        if not self.material_scripts():
            return
        f = self.f
        static = MA.mobj_static(f.char, mobj)
        entry['mobj_static'] = {k: static[k] for k in ('trau', 'trav', 'scau', 'scav', 'scrollu', 'scrollv', 'lfrac')}
        entry['tile'] = MA.tile_info(f.char, mobj)
        sc = self.script_of.get(mobj)
        if sc is None:
            return
        a = MA.play(f.char, f.char_int, sc, static)
        if not a['samples']:
            return
        entry['anim'] = dict(script=sc, frames=a['frames'], loop=a['loop'], loop_start=a['loop_start'],
                             keys={k: MA.to_keys(v, k) for k, v in a['samples'].items()})
        what = ', '.join(MA.PROP[k] for k in a['samples'])
        print(f'  material animation 0x{mobj:X}: {what}; {a["frames"]} frames'
              + (' (loops)' if a['loop'] else ''))

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
            entry.update(untextured=True, mobj=mobj, flags=MF.u16(f.char, mobj + 0x30),
                         state=[[f'{v[0]:08X}', f'{v[1]:08X}'] for k, v in key[7] if not self.model_light(v)],
                         costume_map=[[0, 0]] * self.costumes)
            entry['costume_colors'] = self.costume_colors(mobj)
            c0 = entry['costume_colors'][0].get('prim') if entry['costume_colors'] else None
            prim = int(c0, 16) if c0 else dict(key[7]).get((0xFA, 0), (0, 0xFFFFFFFF))[1]
            entry['color'] = [(prim >> sh) & 255 for sh in (24, 16, 8, 0)]
            ms = bytearray(f.char[mobj:mobj + 0x78])
            ms[0x04:0x08] = ms[0x2C:0x30] = b'\0' * 4
            self.mobj_anim(entry, mobj)
            sig = ('untextured', json.dumps(entry['state']), json.dumps(entry['costume_colors']), bytes(ms),
                   self.script_of.get(mobj) if self.material_scripts() else None)
            if sig in self.mat_sig:
                self.mat_index[key] = self.mat_sig[sig]
                return self.mat_index[key]
        else:
            tile_w, tile_h = desc['size']
            wrap, state = (key[6], key[7]) if key[0] == 'mobj' else (key[5], key[6])
            w, h = MF.texture_dimensions(desc['size'], wrap)
            fmt, siz = desc['tile']
            entry.update(size=[w, h], tile_size=[tile_w, tile_h], fmt=fmt, siz=siz,
                         format=self.FMT.get((fmt, siz), f'{fmt}/{siz}'),
                         wrap=self.wrap_entry(wrap, (tile_w, tile_h), (w, h)),
                         state=[[f'{v[0]:08X}', f'{v[1]:08X}']
                                for k, v in state if not self.model_light(v)])
            if key[0] == 'mobj':
                mobj = key[1]
                info = MF.mobj_textures(f, mobj, (w, h))
                flags = info['flags']
                imgs = self.real_entries(info['images'] if flags & 0x11 else [], MF.row_bytes(w, siz) * h, name, 'image')
                # palette size: what's loaded (the DL's TLUT load, or the MObj's: 256 if sub.siz is 8b)
                n_cols = desc.get('tlut_n') or (256 if f.char[mobj + 3] == 1 else 16)
                if flags & 0x04 and flags & 0x03:    # the MObj loads its palette itself (objdisplay.c)
                    n_cols = 256 if f.char[mobj + 3] == 1 else 16
                entry['palette_colors'] = n_cols
                pals = self.real_entries(info['palettes'] if flags & 0x04 else [], 2 * n_cols, name, 'palette')
                entry.update(mobj=mobj, flags=flags, mobj_fmt=f.char[mobj + 2], mobj_siz=f.char[mobj + 3])
                self.mobj_anim(entry, mobj)
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
                frames = MF.decode_script(f.char, self.script_of.get(mobj)) if self.script_of.get(mobj) else []
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
                   json.dumps(cmap), bytes(ms), json.dumps(entry.get('costume_colors')),
                   # (stages, items: MObjs alike but for their material script are separate materials)
                   self.script_of.get(entry['mobj']) if key[0] == 'mobj' and self.material_scripts() else None)
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
        rgba = MF.decode_texture(self.f.char, img, fmt, siz, w, h, pal)
        if fill:
            rgba = MF.clamp_fill(rgba, *fill)
        rel = f'textures/{name}_{suffix}.png'
        Image.fromarray(rgba, 'RGBA').save(os.path.join(self.out, rel))
        return rel

    # ---- meshes
    def mesh(self, name, geo, owner_of=None, extra=None, preserve_order=False):
        """One glTF mesh per (joint drawing it): vertices rigid on their source joint."""
        by_joint = {}
        for t in geo.tris:
            by_joint.setdefault(t.joint, []).append(t)
        for j, tris in sorted(by_joint.items()):
            pos, nrm, uv, jnt, col = [], [], [], [], []
            prims = []
            for t in tris:
                desc = geo.materials[t.material]
                mi = self.material(t.material, desc, None)
                if t.link:                # drawn from a DObjDLLink list other than 0 (translucent...)
                    self.manifest['materials'][self.gl['materials'][mi]['name']]['dl_link'] = t.link
                wrap = desc['wrap']
                w, h = MF.texture_dimensions(desc['size'], wrap)
                lit = desc['state'].get((0xD9, 0), (0, MF.GEOM_DEFAULT))[1] & MF.G_LIGHTING
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
                        col.append(tuple(MF.srgb_to_linear(x / 255) for x in rgba[:3]) + (rgba[3] / 255,))
                    uv.append(((st[0] - t.tile_ul[0]) / w, (st[1] - t.tile_ul[1]) / h))
                    jnt.append(src)
                    idx.append(len(pos) - 1)
                if preserve_order:
                    if not prims or prims[-1][0] != mi:
                        prims.append([mi, []])
                    prims[-1][1].extend(idx)
                else:
                    primitive = next((p for p in prims if p[0] == mi), None)
                    if primitive is None:
                        primitive = [mi, []]
                        prims.append(primitive)
                    primitive[1].extend(idx)
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
                 'indices': self.add(np.array(ix, '<u4'), UINT, 'SCALAR', 34963)} for mi, ix in prims]})
            self.gl['nodes'].append({'name': mesh_name, 'mesh': len(self.gl['meshes']) - 1, 'skin': self.skin})
            self.gl['scenes'][0]['nodes'].append(len(self.gl['nodes']) - 1)
            obj_info = dict(
                joint=j, triangles=len(tris),
                draw_order=[self.gl['materials'][mi]['name'] for mi, _ in prims], **(extra or {}))
            if preserve_order:
                obj_info['draw_runs'] = [
                    dict(material=self.gl['materials'][mi]['name'], triangles=len(ix) // 3)
                    for mi, ix in prims]
            self.manifest['objects'][mesh_name] = obj_info

    def run(self):
        f = self.f
        if (getattr(f, 'object', None) or {}).get('kind') == 'stage':
            return self.run_stage()
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
                    ms_list = MF.pointer_run(f.char_int, part['mobjsubs']) if part['mobjsubs'] is not None else []
                    if an and an[0] == 'char':
                        for i, ms in enumerate(ms_list):
                            sc = f.char_int.get(an[1] + 4 * i)
                            if sc is not None:
                                self.script_of.setdefault(ms, sc)
        self.skeleton(hi['joints'])
        self.manifest['lights'] = MF.model_lights(f)       # light / shadow (ambient) colors
        self.manifest['expressions'] = f.textureparts      # (texture-index switches: joint + material index)
        for detail in ('hi', 'lo'):
            d = f.details.get(detail)
            if not d:
                continue
            dls = [(j.index, j.dl) for j in d['joints']]
            geo, _ = MF.decode_model(f, d['joints'], d['mobjs'], dls,
                                     carry_state=bool(getattr(f, 'object', None)))
            self.mesh(detail, geo, extra=dict(detail=detail))
            self.manifest.setdefault('mobj_order', {})[detail] = {
                str(j): self.mobj_order(lst) for j, lst in d['mobjs'].items() if lst}
            if self.material_scripts() and d.get('anim') is not None:      # items / projectiles
                anims = []
                if self.joint_animations(detail, d, anims):
                    self.animation('Animation', anims)
        for joint, variants in sorted(f.modelparts.items()):
            for v, pair in enumerate(variants):
                for k, detail in enumerate(('hi', 'lo')):
                    part = pair[k]
                    if part['dl'] is None:
                        continue
                    lst = MF.pointer_run(f.char_int, part['mobjsubs']) if part['mobjsubs'] is not None else []
                    try:
                        geo, _ = MF.decode_model(f, self.joints, {joint: lst}, [(joint, part['dl'])])
                    except MF.BadDL as err:
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
                    geo, _ = MF.decode_model(f, self.joints, {j: hi_mobjs.get(j, [])}, [(j, e['dl'])])
                except MF.BadDL as err:
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
                    best = max(best, len(MF.decode_script(f.char, sc)))
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
    links = list(MF.chain(data, entry.res).items())
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


def main(argv=None):
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
    # items / projectiles (one model described by ITAttributes / WPAttributes)
    ap.add_argument('--object', metavar='NAME',
                    help="CHAR_DIR's (a character or stage folder) item / projectile NAME: config.yaml "
                         "files: NAME_hitbox + NAME_gfx (append_files) or NAME (stage items)")
    ap.add_argument('--attr', help='attributes .bin (with --model, or alone when the model is in it)')
    ap.add_argument('--model', help='model .bin the attributes point into')
    ap.add_argument('--attr-offset', type=lambda v: int(v, 16), help='hex offset of the attributes '
                    '(default: the attributes file\'s external chain head, else 0)')
    ap.add_argument('--attr-heads', default='3FFFC,0', help='hex internal,external chain heads of --attr')
    ap.add_argument('--model-head', type=lambda v: int(v, 16), default=0x3FFFC, help='hex internal chain head of --model')
    ap.add_argument('--item', type=str.upper, choices=VANILLA_ITEMS, help='vanilla common item from --rom')
    stage_names = list(VANILLA_STAGES) + [n for n in remix_stages() if n not in VANILLA_STAGES]
    ap.add_argument('--stage', nargs='?', const=True, default=None, type=str.upper,
                    metavar='{' + ','.join(stage_names) + '}',
                    help='alone: CHAR_DIR is a stage folder (its geometry layers: header.bin -> stage.bin); '
                         'with a name and --rom: a stage to extract from the ROM (vanilla name or File.asm '
                         'STG_<NAME>_HEADER constant), or its header file\'s hex ID')
    a = ap.parse_args(argv)
    if isinstance(a.stage, str) and not a.rom:
        # no ROM: what followed --stage is the stage folder (--stage STAGE_DIR OUT_DIR)
        a.paths.insert(0, os.path.normpath(next(x for x in (sys.argv[1:] if argv is None else argv) if x.upper() == a.stage)))
        a.stage = True
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
    if a.stage:
        if isinstance(a.stage, str):
            f = load_rom_stage(a.rom, a.stage, ap)
        elif a.rom:
            ap.error('--stage with --rom needs the stage\'s name')
        elif not char_dir:
            ap.error('--stage needs the stage folder (STAGE_DIR OUT_DIR)')
        else:
            f = MF.load_stage(**stage_files(cfg, char_dir))
        ex = Exporter(f, out_dir, 1)
        ex.manifest['object'] = f.object
        ex.manifest['source'] = source_info('stage', char_dir, a, a.stage if isinstance(a.stage, str) else None)
        try:                       # collision lines, map objects, spawns, bounds (Blender: 'Collision')
            head = None if a.rom else stage_files(cfg, char_dir)['stage_int_head']
            ex.manifest['stage_collision'] = GD.stage_collision(f.char, head, f.main, f.object['ground'],
                                                                {} if a.rom else cfg)
        except Exception as e:     # (a stage whose geometry isn't found: the model still exports)
            print(f'  note: no collision exported ({e})')
        ex.run()
        m = ex.manifest
        tris = sum(o['triangles'] for o in m['objects'].values())
        print(f"exported layers {f.object['layers']}: {len(m['objects'])} meshes ({tris} triangles), "
              f"{len(m['materials'])} materials, {sum(len(v['images']) for v in m['materials'].values())} image + "
              f"{sum(len(v['palettes']) for v in m['materials'].values())} palette entries -> {out_dir}")
        if not a.no_preview:
            import preview
            for p in preview.save_sheets(out_dir):
                print(f'preview: {p}')
        return
    if a.object or a.attr or a.item:
        f = load_object_args(a, ap, cfg, char_dir, out_dir)
        ex = Exporter(f, out_dir, a.costumes or costumes_in_scripts(f) or 1)
        ex.manifest['object'] = f.object
        ex.manifest['source'] = source_info('object', char_dir if a.object else None, a, a.object or a.item)
        ex.run()
        m = ex.manifest
        tris = sum(o['triangles'] for o in m['objects'].values())
        print(f"exported {'display list' if f.object['dl_only'] else str(len(m['joints'])) + ' joints'}, "
              f"{len(m['objects'])} meshes ({tris} triangles), {len(m['materials'])} materials, "
              f"{sum(len(v['images']) for v in m['materials'].values())} image + "
              f"{sum(len(v['palettes']) for v in m['materials'].values())} palette entries -> {out_dir}")
        if not a.no_preview:
            import preview
            for p in preview.save_sheets(out_dir):
                print(f'preview: {p}')
        return
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
            f = MF.load(main_path, char_path, main_entry.tbl or 0,
                        main_entry.res or 0, char_entry.tbl or 0, char_ext)
    else:
        main_path = a.main or os.path.join(char_dir, 'main.bin')
        char_path = a.char or os.path.join(char_dir, 'character.bin')
        f = MF.load(main_path, char_path, mi, me, ci)

    tf = (cfg.get('attributes') or {}).get('texture_forms') if not a.rom else None
    if tf:   # texture parts the build writes into main.bin (config.yaml attributes.texture_forms)
        seen = {}
        f.textureparts = []
        for k, part in enumerate(tf):
            gj = int(str(part), 16)
            occ = seen.get(gj, 0)
            seen[gj] = occ + 1
            f.textureparts.append(dict(index=k, joint=gj - MF.JOINT_COMMON_START, mobj_hi=occ, mobj_lo=occ,
                                       source='config.yaml texture_forms'))
    n = (a.costumes or cfg.get('num_costumes')
         or (VANILLA_FIGHTER_COSTUMES.get(a.fighter, 4) if a.fighter else None)
         or (1 if a.rom else costumes_in_scripts(f)) or 1)
    ex = Exporter(f, out_dir, n)
    ex.manifest['source'] = source_info('fighter', None if (a.main or a.char) else char_dir, a, a.fighter)
    ex.manifest['hurtboxes'] = GD.hurtboxes(f, {} if a.rom else cfg)     # (Blender: 'Hurtboxes')
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


def source_info(kind, folder, a, name=None):
    """What was exported, for the import (ssb64_model.py import finds the folder to write back to):
    kind fighter / object / stage, the folder it came from (None: a ROM or loose files), its name."""
    return dict(kind=kind, folder=os.path.abspath(folder) if folder and not a.rom else None,
                rom=os.path.abspath(a.rom) if a.rom else None, name=name)


def load_rom_stage(rom, name, ap):
    """A stage's header file (decomp: Map file; MPGroundData at 0x14) from the ROM, and the file its geometry
    layers point into (its external chain: which file each pointer goes to is the
    entry's ID list, in chain order)."""
    key = name.upper()
    remix = remix_stages()
    if key in VANILLA_STAGES:
        header_id = VANILLA_STAGES[key]
    else:
        remix_name = key
        if remix_name.startswith('STG_'):
            remix_name = remix_name[4:]
        if remix_name.endswith('_HEADER'):
            remix_name = remix_name[:-7]
        if key in remix:
            header_id = remix[key]
        elif remix_name in remix:
            header_id = remix[remix_name]
        else:
            try:
                header_id = int(name, 16)
            except ValueError:
                ap.error(f'unknown stage {name!r} (choose from {", ".join(list(VANILLA_STAGES) + list(remix))}, '
                         'or a hex header file ID)')
    entries = SSBtbl.fromROM(N64(open(rom, 'rb').read()))
    if not 0 <= header_id < len(entries):
        ap.error(f'stage header file ID 0x{header_id:X} is outside the ROM file table (0..0x{len(entries) - 1:X})')
    entry = entries[header_id]
    header = entry.extract('data')
    ids = [int.from_bytes(entry.idx[i:i + 2], 'big') for i in range(0, len(entry.idx or b''), 2)]
    links = list(MF.chain(header, entry.res).keys()) if entry.res is not None else []
    layer_files = {ids[links.index(MF.GROUND_DATA + 0x10 * L)] for L in range(MF.GROUND_LAYERS)
                   if MF.GROUND_DATA + 0x10 * L in links}
    if not layer_files:
        raise SystemExit(f'stage header file 0x{header_id:X}: no geometry layers')
    stage_id = min(layer_files)
    if len(layer_files) > 1:
        print(f'  note: layers point into files {sorted(hex(x) for x in layer_files)}; '
              f'exporting those in 0x{stage_id:X}')
    stage_data, stage_ext = rom_external_data(entries, entries[stage_id])
    with tempfile.TemporaryDirectory() as temp_dir:
        hp, sp = os.path.join(temp_dir, 'header.bin'), os.path.join(temp_dir, 'stage.bin')
        open(hp, 'wb').write(header)
        open(sp, 'wb').write(stage_data)
        f = MF.load_stage(hp, sp, entry.tbl if entry.tbl is not None else 0x3FFFC,
                          entry.res if entry.res is not None else 0x3FFFC,
                          entries[stage_id].tbl if entries[stage_id].tbl is not None else 0x3FFFC,
                          stage_ext=stage_ext, only_file=(ids, links, stage_id))
    f.object.update(rom_header=header_id, rom_stage=stage_id, name=name)
    return f


def load_object_args(a, ap, cfg, char_dir, out_dir):
    """MF.load_object from --object / --attr [--model] / --rom --item."""
    if a.item:
        if not a.rom:
            ap.error('--item requires --rom')
        entries = SSBtbl.fromROM(N64(open(a.rom, 'rb').read()))
        entry = entries[ITCOMMON_FILE]
        off = VANILLA_ITEMS[a.item]
        # the attributes point (external chain) into the item model file; which file each
        # external pointer goes to: the entry's ID list, in chain order
        attr = entry.extract('data')
        ids = [int.from_bytes(entry.idx[i:i + 2], 'big') for i in range(0, len(entry.idx or b''), 2)]
        links = list(MF.chain(attr, entry.res).keys()) if entry.res is not None else []
        if off not in links:
            raise SystemExit(f'{a.item}: no model pointer at 0x{off:X}')
        model_id = ids[links.index(off)]
        model_data, model_ext = rom_external_data(entries, entries[model_id])
        with tempfile.TemporaryDirectory() as temp_dir:
            ap_, mp_ = os.path.join(temp_dir, 'attr.bin'), os.path.join(temp_dir, 'model.bin')
            open(ap_, 'wb').write(attr)
            open(mp_, 'wb').write(model_data)
            f = MF.load_object(ap_, mp_, entry.tbl, entry.res, entries[model_id].tbl, off, model_ext)
        f.object.update(kind='item', rom_file=ITCOMMON_FILE, model_file=model_id, name=a.item)
        return f
    if a.object:
        if not char_dir:
            ap.error('--object needs CHAR_DIR (CHAR_DIR OUT_DIR)')
        kw = object_files(cfg, char_dir, a.object)
        if a.attr_offset is not None:
            kw['attr_offset'] = a.attr_offset
        f = MF.load_object(**kw)
        f.object.update(name=a.object, char_dir=char_dir, **{k: v for k, v in kw.items() if k.endswith('path')})
        return f
    ih, eh = (int(x, 16) for x in a.attr_heads.split(','))
    f = MF.load_object(a.attr, a.model, ih, eh, a.model_head,
                       a.attr_offset if a.attr_offset is not None else (eh if a.model and eh < 0x3FFFC else 0))
    f.object.update(attr_path=a.attr, model_path=a.model)
    return f


if __name__ == '__main__':
    main()
