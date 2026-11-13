"""Read an SSB64 / Smash Remix fighter's model data (main.bin + character.bin).

Generic (any fighter): everything is found through the fighter attributes
block in main.bin (layout from the decomp, src/ft/fttypes.h FTAttributes):

  commonparts_container  (attr + 0x2D4) -> FTCommonPart[2] (hi, lo detail):
      DObjDesc *dobjdesc; MObjSub ***p_mobjsubs; AObjEvent32 ***p_costume_matanim; u8 flags
  modelparts_container   (attr + 0x328) -> FTModelPartDesc*[joint]  (alternate parts:
      per joint a list of [variant][hi, lo] FTModelPart {dl, mobjsubs, costume_matanim,
      main_matanim, flags})
  textureparts_container (attr + 0x330) -> FTTexturePart[2] {u8 joint, u8 detail[2]}
      (facial expressions: which joint's which material swaps image)

main.bin pointers into character.bin are external (main's external chain);
pointers inside each file are internal (each file's internal chain).
"""
import struct
from dataclasses import dataclass, field

import numpy as np

ATTR_PATTERN = b'\x00\x64\x00\x64'     # same rule as smashremix_extra.rom_util.get_attrib_offset
ATTR_PATTERN_BACK = 228
A_COMMONPARTS = 0x2D4
A_MODELPARTS = 0x328
A_TEXTUREPARTS = 0x330
A_SKELETON = 0x344
G_CULL_FRONT, G_CULL_BACK, G_LIGHTING = 0x200, 0x400, 0x20000
GEOM_BITS = G_CULL_FRONT | G_CULL_BACK | G_LIGHTING        # what a material keeps of the geometry mode
GEOM_CMD = 0xD9000000 | (~GEOM_BITS & 0xFFFFFF)          # gSPGeometryMode: AND mask (clear those), then set w1
GEOM_DEFAULT = G_LIGHTING | G_CULL_BACK                  # the game's fighter drawing: lit, back faces culled
JOINT_COMMON_START = 4     # player joints[0..3]: TopN, TransN, XRotN, YRotN; DObjDesc i = joints[i + 4]
# modelparts_container is indexed by fighter joint number directly (index 6 = joint 6)
DOBJDESC_SIZE = 0x2C
DOBJDESC_END = 0x12
MODELPART_SIZE = 0x14


def u16(d, o): return struct.unpack_from('>H', d, o)[0]
def u32(d, o): return struct.unpack_from('>I', d, o)[0]
def f32(d, o): return struct.unpack_from('>f', d, o)[0]


def chain(d, head):
    """{field offset: target offset} of a relocation chain starting at head."""
    out, off, seen = {}, head, set()
    while 0 <= off <= len(d) - 4 and off not in seen:
        seen.add(off)
        nxt, tgt = u16(d, off), u16(d, off + 2)
        out[off] = tgt * 4
        if nxt == 0xFFFF or nxt * 4 >= len(d):
            break
        off = nxt * 4
    return out


def attributes_offset(main):
    pos = main.rfind(ATTR_PATTERN)
    if pos < 0:
        raise ValueError('fighter attributes not found in main.bin')
    return pos - ATTR_PATTERN_BACK


def rot_zyx(r, p, h):
    cx, sx, cy, sy, cz, sz = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(h), np.sin(h)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


@dataclass
class Joint:
    index: int
    depth: int
    parent: int
    dl: int                  # character.bin offset of its display list (or None)
    t: np.ndarray
    r: np.ndarray
    s: np.ndarray
    world: np.ndarray = None


@dataclass
class Fighter:
    main: bytes
    char: bytes
    main_int: dict
    main_ext: dict
    char_int: dict
    attr: int
    details: dict = field(default_factory=dict)      # 'hi'/'lo' -> dict(joints, mobjs, matanims)
    modelparts: dict = field(default_factory=dict)   # joint -> [[hi part, lo part], ...]
    textureparts: list = field(default_factory=list)
    skeletons: dict = field(default_factory=dict)    # electric-shock skeleton id -> {joint: entry}

    def ptr_main(self, field_off):
        """Where a main.bin pointer field points: ('char', off) / ('main', off) / None."""
        if field_off in self.main_ext:
            return ('char', self.main_ext[field_off])
        if field_off in self.main_int:
            return ('main', self.main_int[field_off])
        return None

    def ptr_char(self, field_off):
        return self.char_int.get(field_off)


def read_skeleton(char, char_int, arr):
    joints, e, stack = [], arr, {}
    while u32(char, e) != DOBJDESC_END and len(joints) < 64:
        depth = u32(char, e)
        v = struct.unpack_from('>9f', char, e + 8)
        parent = stack.get(depth - 1) if depth > 0 else None
        j = Joint(len(joints), depth, parent, char_int.get(e + 4),
                  np.array(v[:3]), np.array(v[3:6]), np.array(v[6:]))
        stack[depth] = j.index
        M = np.eye(4)
        M[:3, :3] = rot_zyx(*j.r) * j.s
        M[:3, 3] = j.t
        j.world = M if parent is None else joints[parent].world @ M
        joints.append(j)
        e += DOBJDESC_SIZE
    return joints


def pointer_list(d, ptrs, start, count):
    """Entries of an array of pointers (None where a field isn't relocated)."""
    return [ptrs.get(start + 4 * i) for i in range(count)]


def load(main_path, char_path, main_int_head=0x2CC, main_ext_head=0x0, char_int_head=0x4, char_ext=None):
    """char_ext: extra character.bin pointers {field offset: target offset}, for data
    appended to character.bin (ROM exports: other files its external chain points into,
    e.g. the bone texture every vanilla electric-shock skeleton shares)."""
    main = open(main_path, 'rb').read()
    char = open(char_path, 'rb').read()
    char_int = chain(char, char_int_head)
    char_int.update(char_ext or {})
    f = Fighter(main, char, chain(main, main_int_head), chain(main, main_ext_head),
                char_int, attributes_offset(main))
    # ---- common parts (hi, lo)
    cp = f.ptr_main(f.attr + A_COMMONPARTS)
    if cp is None or cp[0] != 'main':
        raise ValueError('commonparts_container not found')
    for k, detail in enumerate(('hi', 'lo')):
        base = cp[1] + 0x10 * k
        dobj = f.ptr_main(base)
        mobj = f.ptr_main(base + 4)
        anim = f.ptr_main(base + 8)
        if dobj is None:
            continue
        joints = read_skeleton(char, f.char_int, dobj[1])
        n = len(joints)
        # p_mobjsubs: array (one per joint) of NULL-terminated MObjSub* lists (in character.bin)
        mobjs = {}
        if mobj is not None:
            arr = mobj[1]
            for j in range(n):
                lst = f.char_int.get(arr + 4 * j)
                items = []
                while lst is not None and lst + 4 * len(items) in f.char_int:
                    items.append(f.char_int[lst + 4 * len(items)])
                mobjs[j] = items
        f.details[detail] = dict(dobjdesc=dobj[1], joints=joints, mobjs=mobjs,
                                 matanim=anim[1] if anim else None)
    # ---- model parts (alternate pieces per joint)
    mp = f.ptr_main(f.attr + A_MODELPARTS)
    if mp is not None and mp[0] == 'main':
        nj = len(f.details['hi']['joints'])
        descs = {i: f.main_int[mp[1] + 4 * i] for i in range(nj) if mp[1] + 4 * i in f.main_int}
        # a joint's variant list has no stored length: it runs until the next joint's
        # list (or the container itself), and stops at the first pair without a display list
        starts = sorted(set(descs.values()) | {mp[1]})
        for i, desc in sorted(descs.items()):
            end = next((x for x in starts if x > desc), len(main))
            variants, off = [], desc
            while off + MODELPART_SIZE * 2 <= end:
                pair = []
                for k in range(2):
                    b = off + MODELPART_SIZE * k
                    dl = f.ptr_main(b)
                    ms = f.ptr_main(b + 4)
                    pair.append(dict(field=b, dl=dl[1] if dl and dl[0] == 'char' else None,
                                     mobjsubs=ms[1] if ms and ms[0] == 'char' else None,
                                     flags=main[b + 0x10]))
                if pair[0]['dl'] is None and pair[1]['dl'] is None:
                    break
                variants.append(pair)
                off += MODELPART_SIZE * 2
            if variants:
                f.modelparts[i] = variants
    # ---- electric-shock skeleton models: attr->skeleton[id][joint] = {dl, flags} (8 bytes),
    # id 0 unused; drawn with the joint's MObjs (flags & 0xF: 0 = one DL, 1 = two-DL array)
    sk = f.ptr_main(f.attr + A_SKELETON)
    if sk is not None and sk[0] == 'main':
        nj = len(f.details['hi']['joints'])
        for sid in range(1, 4):
            tab = f.ptr_main(sk[1] + 4 * sid)
            if tab is None or tab[0] != 'main':
                continue
            ents = {}
            for j in range(nj):
                fld = tab[1] + 8 * j
                dl = f.ptr_main(fld)
                if dl is not None and dl[0] == 'char':
                    ents[j] = dict(field=fld, dl=dl[1], flags=main[fld + 4])
            if ents:
                f.skeletons[sid] = ents
    # ---- texture parts (expressions)
    tp = f.ptr_main(f.attr + A_TEXTUREPARTS)
    if tp is not None and tp[0] == 'main':
        for k in range(2):
            o = tp[1] + 3 * k
            game_joint, d0, d1 = main[o], main[o + 1], main[o + 2]
            joint = game_joint - JOINT_COMMON_START                 # our joint index (DObjDesc entry)
            hi_lst = f.details['hi']['mobjs'].get(joint, [])
            if game_joint < JOINT_COMMON_START or d0 >= len(hi_lst):  # unused slot
                continue
            f.textureparts.append(dict(index=k, joint=joint, mobj_hi=d0, mobj_lo=d1))
    return f


# ---------------------------------------------------------------- textures
def load_object(attr_path, model_path=None, attr_int_head=None, attr_ext_head=None,
                model_int_head=None, attr_offset=0, model_ext=None):
    """An item / projectile model, described by its attributes (ITAttributes /
    WPAttributes: +0 data = DObjDesc array or a bare display list, +4 p_mobjsubs,
    +8 anim_joints, +0xC p_matanim_joints). The attributes are in attr_path at
    attr_offset; the model is in model_path (Remix *_hitbox.bin -> *_gfx.bin, through
    the attributes file's external chain), or in the same file (vanilla items,
    model_path None). Returns a Fighter with one model ('hi') and no fighter extras:
    the exporter / importer treat it like a one-costume fighter."""
    attr = open(attr_path, 'rb').read()
    a_int = chain(attr, attr_int_head) if attr_int_head is not None and attr_int_head < 0x3FFFC else {}
    a_ext = chain(attr, attr_ext_head) if attr_ext_head is not None and attr_ext_head < 0x3FFFC else {}
    if model_path is None:
        char, char_int = attr, dict(a_int)
        ptrs = a_int
    else:
        char = open(model_path, 'rb').read()
        char_int = chain(char, model_int_head) if model_int_head is not None and model_int_head < 0x3FFFC else {}
        ptrs = a_ext
    char_int.update(model_ext or {})
    f = Fighter(attr, char, a_int, a_ext if model_path else {}, char_int, attr_offset)
    data = ptrs.get(attr_offset)
    if data is None:
        raise ValueError(f'no model: the attributes at 0x{attr_offset:X} have no data pointer')
    ms_arr, ma_arr = ptrs.get(attr_offset + 4), ptrs.get(attr_offset + 0xC)
    if char[data] in DL_START_OPS:          # a bare display list: one joint at the origin
        joints = [Joint(0, 0, None, data, np.zeros(3), np.zeros(3), np.ones(3), np.eye(4))]
        dobjdesc = None
    else:
        joints = read_skeleton(char, char_int, data)
        dobjdesc = data
    mobjs = {}
    if ms_arr is not None:
        for j in range(len(joints)):
            lst = char_int.get(ms_arr + 4 * j)
            items = []
            while lst is not None and lst + 4 * len(items) in char_int:
                items.append(char_int[lst + 4 * len(items)])
            mobjs[j] = items
    f.details['hi'] = dict(dobjdesc=dobjdesc, joints=joints, mobjs=mobjs, matanim=ma_arr,
                           anim=ptrs.get(attr_offset + 8) if dobjdesc is not None else None,
                           field=attr_offset + 4)        # (field + 4: anim_joints, like a stage layer's)
    f.object = dict(attr=attr_offset, single_file=model_path is None, dl_only=dobjdesc is None)
    return f


GROUND_DATA = 0x14          # MPGroundData in a Remix stage header.bin (after its 0x14-byte preamble)
GROUND_LAYERS = 4           # gr_desc[4]: {DObjDesc*, anim_joints, p_mobjsubs, p_matanim_joints}


def load_stage(header_path, stage_path, header_int_head, header_ext_head, stage_int_head,
               ground=GROUND_DATA, stage_ext=None, only_file=None):
    """A stage's geometry layers (MPGroundData.gr_desc[0..3] in header.bin, pointing into
    stage.bin through the header's external chain) -> a Fighter whose details are
    'L0'..'L3' (only the layers that have a model), each like an item's single model."""
    header = open(header_path, 'rb').read()
    char = open(stage_path, 'rb').read()
    h_int = chain(header, header_int_head) if header_int_head < 0x3FFFC else {}
    h_ext = chain(header, header_ext_head) if header_ext_head < 0x3FFFC else {}
    char_int = chain(char, stage_int_head) if stage_int_head < 0x3FFFC else {}
    char_int.update(stage_ext or {})
    if only_file:              # ROM: (ID list, external fields in chain order, stage file): its pointers only
        ids, links, fid = only_file
        h_ext = {k: v for k, v in h_ext.items() if k in links and ids[links.index(k)] == fid}
    f = Fighter(header, char, h_int, h_ext, char_int, ground)
    layers = []
    for L in range(GROUND_LAYERS):
        base = ground + 0x10 * L
        data = h_ext.get(base)
        if data is None:
            continue
        joints = read_skeleton(char, char_int, data)
        ms_arr, ma_arr = h_ext.get(base + 8), h_ext.get(base + 0xC)
        mobjs = {}
        if ms_arr is not None:
            for j in range(len(joints)):
                lst = char_int.get(ms_arr + 4 * j)
                items = []
                while lst is not None and lst + 4 * len(items) in char_int:
                    items.append(char_int[lst + 4 * len(items)])
                mobjs[j] = items
        f.details[f'L{L}'] = dict(dobjdesc=data, joints=joints, mobjs=mobjs, matanim=ma_arr,
                                  anim=h_ext.get(base + 4), field=base)
        layers.append(L)
    f.object = dict(kind='stage', ground=ground, layers=layers, single_file=False, dl_only=False)
    return f


# ---------------------------------------------------------------- DObj animation (AObjEvent32)
ANIM_TRACKS = 10            # flag bits -> tracks: rot x/y/z, interp (TraI), tra x/y/z, sca x/y/z


def _anim_value(a):
    """gcPlayDObjAnimJoint's value of one track (AObj) at its current length."""
    if a['kind'] == 'linear':
        return a['vb'] + a['len'] * a['rb']
    if a['kind'] == 'cubic':
        li, ln = a['li'], a['len']
        f16, f12 = li * li, ln * ln
        f18 = li * f12
        f14 = ln * f12 * f16
        f20 = 2.0 * f14 * li
        f22 = 3.0 * f12 * f16
        f24 = f14 - f18
        return (a['vb'] * ((f20 - f22) + 1.0) + a['vt'] * (f22 - f20) +
                a['rb'] * ((f24 - f18) + ln) + a['rt'] * f24)
    if a['kind'] == 'step':
        return a['vt'] if a['li'] <= a['len'] else a['vb']
    return None


def simulate_anim(d, ptrs, script, frames):
    """A DObj animation script (anim_joints[joint]: AObjEvent32 commands, objanim.c
    gcParseDObjAnimJoint + gcPlayDObjAnimJoint) played for `frames` frames ->
    [{track: value}] per frame (only the animated tracks: 0..2 rotation x/y/z, 4..6
    translation, 7..9 scale), and the frame its script first loops back (or ends), if any."""
    END = 'end'
    tracks, out = {}, []
    wait, frame, ptr, seen, loop_at = 'changed', 0.0, script, {script}, None

    def track(i):
        return tracks.setdefault(i, dict(kind=None, vb=0.0, vt=0.0, rb=0.0, rt=0.0, len=0.0, li=1.0))

    def f32_at(o):
        return f32(d, o)
    for n in range(frames):
        # ---- parse
        if wait != END:
            if wait == 'changed':
                wait = -frame
            else:
                wait -= 1.0
                frame += 1.0
            while wait != END and wait <= 0.0:
                if ptr is None or ptr + 4 > len(d):
                    for a in tracks.values():
                        if a['kind']:
                            a['len'] += 1.0 + wait
                    wait = END
                    loop_at = loop_at if loop_at is not None else n
                    break
                w = u32(d, ptr)
                op, payload = w >> 25, w & 0x7FFF
                if op in (3, 4, 5, 6, 8, 9, 10, 11):     # set value (+ rate): block / not
                    flags = (u32(d, ptr) >> 15) & 0x3FF
                    ptr += 4
                    for i in range(ANIM_TRACKS):
                        if not flags >> i:
                            break
                        if not (flags >> i) & 1:
                            continue
                        a = track(i)
                        a['vb'], a['vt'] = a['vt'], f32_at(ptr)
                        ptr += 4
                        if op in (5, 6):                          # value + rate (cubic)
                            a['rb'], a['rt'] = a['rt'], f32_at(ptr)
                            ptr += 4
                            a['kind'] = 'cubic'
                            if payload:
                                a['li'] = 1.0 / payload
                        elif op in (8, 9):                        # value, rate 0 (cubic)
                            a['rb'], a['rt'] = a['rt'], 0.0
                            a['kind'] = 'cubic'
                            if payload:
                                a['li'] = 1.0 / payload
                        elif op in (3, 4):                        # linear
                            a['kind'] = 'linear'
                            if payload:
                                a['rb'] = (a['vt'] - a['vb']) / payload
                            a['rt'] = 0.0
                        else:                                     # 10, 11: value after (step)
                            a['kind'] = 'step'
                            a['li'] = float(payload)
                            a['rt'] = 0.0
                        a['len'] = -wait - 1.0
                    if op in (3, 5, 8, 10):                       # the *Block variants wait
                        wait += payload
                elif op == 7:                                     # target rate
                    flags = (u32(d, ptr) >> 15) & 0x3FF
                    ptr += 4
                    for i in range(ANIM_TRACKS):
                        if not flags >> i:
                            break
                        if (flags >> i) & 1:
                            track(i)['rt'] = f32_at(ptr)
                            ptr += 4
                elif op == 2:                                     # wait
                    wait += payload
                    ptr += 4
                elif op in (1, 14):                               # jump / set anim
                    target = ptrs.get(ptr + 4)
                    if target in seen and loop_at is None:
                        loop_at = n
                    seen.add(target)
                    ptr = target
                    if op == 14:
                        frame = -wait
                elif op == 12:
                    flags = (u32(d, ptr) >> 15) & 0x3FF
                    ptr += 4
                    for i in range(ANIM_TRACKS):
                        if not flags >> i:
                            break
                        if (flags >> i) & 1:
                            track(i)['len'] += payload
                elif op == 13:                                    # interp path (TraI): not shown
                    ptr += 8
                elif op == 0:                                     # end
                    for a in tracks.values():
                        if a['kind']:
                            a['len'] += 1.0 + wait
                    wait = END
                    loop_at = loop_at if loop_at is not None else n
                elif op in (15, 16):
                    wait += payload
                    ptr += 4
                elif op == 17:
                    flags = (u32(d, ptr) >> 15) & 0x3FF
                    wait += payload
                    ptr += 4
                    for i in range(ANIM_TRACKS):
                        if not flags >> i:
                            break
                        if (flags >> i) & 1:
                            ptr += 4
                else:                                             # not a joint command: stop
                    wait = END
                    loop_at = loop_at if loop_at is not None else n
        # ---- play
        vals = {}
        for i, a in tracks.items():
            if a['kind']:
                if wait != END:
                    a['len'] += 1.0
                if i != 3:
                    vals[i] = _anim_value(a)
        out.append(vals)
    return out, loop_at


def rgba16(v):
    return (((v >> 11) & 31) * 255 // 31, ((v >> 6) & 31) * 255 // 31,
            ((v >> 1) & 31) * 255 // 31, 255 * (v & 1))


def row_bytes(w, siz):
    """Bytes per texture row in memory: rows are 64-bit aligned (a texture memory line),
    so narrow textures (e.g. 8 px CI4 = 4 bytes) are stored padded to 8 bytes per row."""
    return (w * (4, 8, 16, 32)[siz] // 8 + 7) // 8 * 8


def texture_dimensions(tile_size, wrap):
    """Source image dimensions from the render tile's masks, falling back to its extents."""
    w, h = tile_size
    mask_s, mask_t = wrap[2:4]
    return (1 << mask_s if mask_s else w, 1 << mask_t if mask_t else h)


def clamp_fill(rgba, wrap, extent):
    """Texels past a clamped tile's extent (never drawn: the tile clamps before them,
    e.g. DK's tie: a 24x20 tile in a 32x32 texture, whatever data follows in the rest)
    replaced by the edge texel, as the game samples them. wrap: the manifest's
    ('clamp' per axis), extent: the tile's (w, h)."""
    if not wrap or extent is None:
        return rgba
    out, (ew, eh) = rgba, extent
    if wrap.get('s') == 'clamp' and 0 < ew < out.shape[1]:
        out = out.copy()
        out[:, ew:] = out[:, ew - 1:ew]
    if wrap.get('t') == 'clamp' and 0 < eh < out.shape[0]:
        out = out.copy()
        out[eh:, :] = out[eh - 1:eh, :]
    return out


def decode_texture(d, addr, fmt, siz, w, h, pal_addr, stride=None):
    """N64 texture -> RGBA array (CI4/CI8/RGBA16/RGBA32/IA4/IA8/IA16/I4/I8).
    stride: bytes per row (default: 64-bit aligned rows, see row_bytes)."""
    bpp = (4, 8, 16, 32)[siz]
    stride = stride or row_bytes(w, siz)
    pal = None
    if fmt == 2 and pal_addr is not None:
        n = 256 if siz == 1 else 16
        pal = np.array([rgba16(u16(d, pal_addr + 2 * i)) if pal_addr + 2 * i + 2 <= len(d) else (255, 0, 255, 255)
                        for i in range(n)], np.uint8)
    out = np.zeros((h, w, 4), np.uint8)
    for y in range(h):
        for x in range(w):
            bit = y * stride * 8 + x * bpp
            o = addr + bit // 8
            if o >= len(d):
                continue
            if bpp == 4:
                v = (d[o] >> (4 if bit % 8 == 0 else 0)) & 15
            elif bpp == 8:
                v = d[o]
            elif bpp == 16:
                v = u16(d, o)
            else:
                v = u32(d, o)
            if fmt == 2:
                out[y, x] = pal[v] if pal is not None and v < len(pal) else (255, 0, 255, 255)
            elif fmt == 0:
                out[y, x] = rgba16(v) if bpp == 16 else [(v >> s) & 255 for s in (24, 16, 8, 0)]
            elif fmt == 3:
                if bpp == 4:
                    i, a = (v >> 1) * 255 // 7, 255 * (v & 1)
                elif bpp == 8:
                    i, a = (v >> 4) * 17, (v & 15) * 17
                else:
                    i, a = v >> 8, v & 255
                out[y, x] = (i, i, i, a)
            else:
                i = v * 17 if bpp == 4 else v
                out[y, x] = (i, i, i, 255)
    return out


# ---------------------------------------------------------------- display lists
@dataclass
class Tri:
    joint: int               # the joint whose DL draws it
    corners: list            # [(source joint, local xyz, normal xyz, st texels, rgba bytes)] x3
    material: tuple          # key into Geometry.materials
    tile_ul: tuple = (0.0, 0.0)   # render tile's upper-left (texels) when drawn: the RDP subtracts it from st
    link: int = 0                 # DObjDLLink list id of the DL drawing it (0: opaque / plain DL)


@dataclass
class Geometry:
    tris: list = field(default_factory=list)
    materials: dict = field(default_factory=dict)   # key -> description


def decode_model(f, joints, mobjs, dls, cache=None, carry_state=False):
    """Triangles of the given display lists, drawn in order with one shared RSP
    vertex cache (joints reuse their parent's cached vertices: knees, elbows...).
    dls: [(joint index, dl offset)]."""
    char, ptrs = f.char, f.char_int
    link_of = {}
    for joint, dl in dls:
        for lid, target in (dl_links(char, ptrs, dl) or []) if dl is not None else []:
            link_of[target] = lid
    dls = [(joint, target) for joint, dl in dls if dl is not None
           for target in linked_display_lists(char, ptrs, dl)]
    geo = Geometry()
    cache = cache if cache is not None else [None] * 32
    st = None
    for j, dl in dls:
        # geometry mode: the game draws fighters lit, with back-face culling on
        if st is None or not carry_state:
            st = dict(img=None, tile=(2, 0), size=(32, 32), ul=(0.0, 0.0), scale=(1.0, 1.0),
                      prim=(255, 255, 255, 255), tex_on=False,
                      mobj=None, pal=None, wrap=(0, 0, 0, 0), geom=GEOM_DEFAULT,
                      state={(0xD9, 0): (GEOM_CMD, GEOM_DEFAULT)})
        else:
            # carry_state (stages, items): the game draws a model's joints one after the
            # other, so a joint's DL starts with what the previous one left set (vanilla
            # stages set texturing / the combiner once, in their first joint). Its MObjs
            # are its own.
            st['mobj'], st['drawn'] = None, False

        def key():
            if not st['tex_on']:
                if st['mobj'] is not None:     # untextured, colors from the MObj (may change per costume)
                    return ('mobj', st['mobj'], None, None, None, None, None, tuple(sorted(st['state'].items())))
                return ('prim',) + tuple(st['prim']) + (tuple(sorted(st['state'].items())),)
            if st['mobj'] is not None:     # material object (+ inline image/palette it may not supply)
                return ('mobj', st['mobj'], st['tile'], st['size'], st['img'], st['pal'], st['wrap'],
                        tuple(sorted(st['state'].items())))
            return ('tex', st['img'], st['pal'], st['tile'], st['size'], st['wrap'],
                    tuple(sorted(st['state'].items())))

        def run(o, depth=0):
            while o + 8 <= len(char):
                w0, w1 = u32(char, o), u32(char, o + 4)
                op = w0 >> 24
                if st.get('drawn') and (op in (0xF5, 0xFD, 0xF0, 0xE8, 0xD7) or
                                        (op == 0xDE and (w1 >> 24) == 0x0E)):
                    # a new material is being set up: an MObj called for the last one doesn't apply
                    st['mobj'], st['drawn'] = None, False
                if op == 0xFD:                                   # SETTIMG
                    st['img'] = ptrs.get(o + 4)
                elif op == 0xF0:                                 # LOADTLUT
                    st['pal'] = st['img']
                    st['tlut_n'] = ((w1 >> 14) & 0x3FF) + 1      # colors loaded
                elif op == 0xF5 and ((w1 >> 24) & 7) == 0:      # SETTILE render tile
                    st['tile'] = ((w0 >> 21) & 7, (w0 >> 19) & 3)
                    # clamp/mirror bits (cm: 1 = mirror, 2 = clamp) and wrap masks, per axis
                    st['wrap'] = ((w1 >> 8) & 3, (w1 >> 18) & 3, (w1 >> 4) & 15, (w1 >> 14) & 15)
                elif op in (0xE2, 0xE3, 0xFC, 0xFB, 0xF9):        # render state (blend mode, combiner, colors)
                    k = (op, (w0 >> 8) & 0xFF) if op in (0xE2, 0xE3) else (op, 0)
                    st['state'] = dict(st['state'])
                    st['state'][k] = (w0, w1)
                elif op == 0xF2 and ((w1 >> 24) & 7) == 0:      # SETTILESIZE render tile
                    # 10.2 fixed point; vanilla fighters use a non-zero upper-left to shift the
                    # texture (Remix ones keep it at 0). size = the tile's extent from that corner.
                    uls, ult = ((w0 >> 12) & 0xFFF) / 4, (w0 & 0xFFF) / 4
                    lrs, lrt = ((w1 >> 12) & 0xFFF) / 4, (w1 & 0xFFF) / 4
                    st['ul'] = (uls, ult)
                    st['size'] = (int(lrs - uls) + 1, int(lrt - ult) + 1)
                elif op == 0xFA:
                    st['prim'] = (w1 >> 24, (w1 >> 16) & 255, (w1 >> 8) & 255, w1 & 255)
                    if st['mobj'] is not None and st.get('drawn') and not st['tex_on']:
                        # a new color after an MObj's last material was drawn (vanilla Mario's hair,
                        # nose, ears after the cap): the MObj set prim when it was called, this
                        # replaces it, and nothing calls the MObj again: it no longer applies
                        st['mobj'], st['drawn'] = None, False
                    st['state'] = dict(st['state'])
                    st['state'][(0xFA, 0)] = (w0, w1)
                elif op == 0xD9:                                 # geometry mode: keep lighting + culling
                    st['geom'] = (st['geom'] & (w0 | 0xFF000000)) | w1
                    st['state'] = dict(st['state'])
                    st['state'][(0xD9, 0)] = (GEOM_CMD, st['geom'] & GEOM_BITS)
                elif op == 0xDB and (w0 & 0xFFFF0000) == 0xDB0A0000:   # light colors (per material too)
                    st['state'] = dict(st['state'])
                    st['state'][(0xDB, w0 & 0xFFFF)] = (w0, w1)
                elif op == 0xD7:
                    st['tex_on'] = bool((w0 >> 1) & 0x7F)
                    if st['tex_on']:     # S/T scale (0.16; 0xFFFF = 1.0), applied by the RSP at vertex load
                        st['scale'] = tuple(1.0 if v == 0xFFFF else v / 65536 for v in (w1 >> 16, w1 & 0xFFFF))
                elif op == 0x01:                                 # VTX
                    n = (w0 >> 12) & 0xFF
                    v0 = ((w0 & 0xFF) >> 1) - n
                    a = ptrs.get(o + 4)
                    if v0 < 0 or v0 + n > len(cache):
                        raise BadDL(f'vertex load {n} at slot {v0} does not fit the vertex cache (0x{o:X})')
                    if a is not None:
                        for k in range(n):
                            x, y, z, _, s, t = struct.unpack_from('>hhhHhh', char, a + 16 * k)
                            nx, ny, nz = struct.unpack_from('>bbb', char, a + 16 * k + 12)
                            rgba = tuple(char[a + 16 * k + 12:a + 16 * k + 16])   # = color when unlit
                            ss, ts = st['scale']
                            cache[v0 + k] = (j, (x, y, z), (nx, ny, nz), (s / 32.0 * ss, t / 32.0 * ts), rgba)
                elif op == 0x02:                                 # MODIFYVTX: edit a cached vertex
                    # vanilla fighters re-texture vertices they share with the parent joint
                    # (DK's belly / tie seams): G_MWO_POINT_ST (0x14) sets s,t (s10.5, written
                    # straight into the vertex buffer: no texture scale), 0x10 sets the color
                    where, k = (w0 >> 16) & 0xFF, (w0 & 0xFFFF) // 2
                    if k < len(cache) and cache[k] is not None:
                        cj, xyz, n3, stc, rgba = cache[k]
                        if where == 0x14:
                            s, t = struct.unpack('>hh', struct.pack('>I', w1))
                            cache[k] = (cj, xyz, n3, (s / 32.0, t / 32.0), rgba)
                        elif where == 0x10:
                            rgba = tuple(struct.pack('>I', w1))
                            n3 = tuple(b - 256 if b > 127 else b for b in rgba[:3])
                            cache[k] = (cj, xyz, n3, stc, rgba)
                elif op in (0x05, 0x06):
                    st['drawn'] = True
                    tl = [[(w0 >> 16 & 0xFF) // 2, (w0 >> 8 & 0xFF) // 2, (w0 & 0xFF) // 2]]
                    if op == 0x06:
                        tl.append([(w1 >> 16 & 0xFF) // 2, (w1 >> 8 & 0xFF) // 2, (w1 & 0xFF) // 2])
                    k = key()
                    if k not in geo.materials:
                        geo.materials[k] = dict(st)
                    for t in tl:
                        cs = [cache[i] for i in t]
                        if all(c is not None for c in cs):
                            geo.tris.append(Tri(j, cs, k, st['ul'], link_of.get(dl, 0)))
                elif op == 0xDE:
                    if (w1 >> 24) == 0x0E:                       # MObj material (segment 0x0E)
                        i = (w1 & 0xFFFFFF) // 8
                        lst = mobjs.get(j, []) if isinstance(mobjs, dict) else mobjs
                        st['mobj'] = lst[i] if i < len(lst) else None
                        if carry_state and st['mobj'] is not None and u16(char, st['mobj'] + 0x30) & 0x20:
                            # (stages, items) the MObj sets the tile's position itself (its offset,
                            # maybe animated: matanim.py): the UVs are the vertices' own
                            st['ul'] = (0.0, 0.0)
                    elif o + 4 in ptrs and depth < 8:
                        run(ptrs[o + 4], depth + 1)
                        if (w0 >> 16) & 0xFF == 1:               # branch
                            return
                elif op == 0xDF:
                    return
                o += 8
        if dl is not None:
            if char[dl] not in DL_START_OPS:
                raise BadDL(f'0x{dl:X} does not start like a display list ({char[dl:dl + 8].hex()})')
            run(dl)
    return geo, cache


class BadDL(ValueError):
    """A pointer that should be a display list isn't one (dummy / unused slots)."""


# commands a fighter display list starts with (sync, geometry mode, lights, combiner, render
# mode, texture, color, vertex, call)
DL_START_OPS = {0xE7, 0xD9, 0xDB, 0xD7, 0xFC, 0xE2, 0xE3, 0xDE, 0x01, 0xF5, 0xFD, 0xFA, 0xFB, 0xE8, 0xE6, 0xDF}


def dl_links(char, ptrs, offset):
    """A DObjDLLink array (GoldEditor models: {u32 list id, Gfx* dl} entries ending with
    list id 4; 0 = opaque, 1 = translucent...) -> [(list id, dl)], or None if offset
    isn't one."""
    if offset in ptrs or offset + 4 not in ptrs or u32(char, offset) > 3:
        return None
    out, o = [], offset
    while o + 8 <= len(char) and len(out) < 8:
        lid = u32(char, o)
        if lid == 4:
            return out
        if lid > 3 or o + 4 not in ptrs:
            return None
        out.append((lid, ptrs[o + 4]))
        o += 8
    return None


def linked_display_lists(char, ptrs, offset):
    """Resolve a direct display list, a DObjDLLink array (every list) or the two-list form."""
    if offset < len(char) and char[offset] in DL_START_OPS:
        return [offset]
    links = dl_links(char, ptrs, offset)
    if links:
        return [dl for _, dl in links]

    linked = []
    for field in (offset, offset + 4):
        target = ptrs.get(field)
        if target in (None, 0):
            continue
        if target >= len(char) or char[target] not in DL_START_OPS:
            raise BadDL(f'DObjDLLink at 0x{offset:X} has invalid display list pointer '
                        f'0x{target:X}')
        linked.append(target)
    if linked:
        return linked
    raise BadDL(f'0x{offset:X} does not start like a display list or DObjDLLink '
                f'({char[offset:offset + 8].hex()})')


_TARGETS = {}


def pointer_run(ptrs, start, limit=64):
    """Entries of a pointer array (no stored length): consecutive relocated
    fields from start, stopping where another array begins (an offset that
    something else points to)."""
    key = id(ptrs)
    if key not in _TARGETS:
        _TARGETS[key] = set(ptrs.values())
    targets = _TARGETS[key]
    out = []
    while start is not None and start + 4 * len(out) in ptrs and len(out) < limit:
        if out and start + 4 * len(out) in targets:
            break
        out.append(ptrs[start + 4 * len(out)])
    return out


COLOR_TRACKS = ('prim', 'env', 'blend', 'light1', 'light2')   # ext-value flag bits 0..4


def decode_script(d, addr, limit=200):
    """AObjEvent32 costume script -> one dict per frame (= costume): 'tex' / 'pal'
    (indices) and 'prim' / 'env' / 'blend' / 'light1' / 'light2' (RGBA u32 colors).
    Event word: opcode:7 | flags:10 | payload:15. SetVal* (3-11): flags 0x001 texture,
    0x200 palette, float values in flag-bit order. SetExtVal* (18-21): flag bits 0-4 =
    prim / env / blend / light1 / light2 color words. *After* events take effect
    `payload` frames later; *Block* events also advance time by `payload`."""
    sets, t, o = [], 0, addr
    for _ in range(limit):
        if o + 4 > len(d):
            break
        w = u32(d, o)
        op, fl, payload = w >> 25, (w >> 15) & 0x3FF, w & 0x7FFF
        o += 4
        if op == 0:
            break
        if op == 1:                              # jump: a costume script never needs it
            break
        if op == 2:                              # wait
            t += payload
            continue
        if op in (3, 4, 5, 6, 7, 8, 9, 10, 11):
            at = t + payload if op in (10, 11) else t
            for b in range(10):
                if fl >> b & 1:
                    v = f32(d, o)
                    o += 4
                    if b == 9:
                        sets.append((at, 'pal', int(round(v))))
                    elif b == 0:
                        sets.append((at, 'tex', int(round(v))))
            if op in (3, 5, 8, 10):              # *Block: time moves on
                t += payload
        elif op in (18, 19, 20, 21):
            at = t + payload if op in (18, 19) else t
            for b in range(5):
                if fl >> b & 1:
                    sets.append((at, COLOR_TRACKS[b], u32(d, o)))
                    o += 4
            if op in (18, 20):
                t += payload
        else:                                    # other events: skip their values
            o += 4 * bin(fl).count('1')
    if not sets:
        return []
    n = max(tm for tm, _, _ in sets) + 1
    frames, cur = [], {}
    for c in range(n):
        for tm, k, v in sets:
            if tm == c:
                cur[k] = v
        frames.append(dict(cur))
    return frames


def mobj_textures(f, mobj, size, n_entries=8):
    """Image and palette pointer arrays of an MObjSub (None where absent)."""
    d, ptrs = f.char, f.char_int
    fmt, siz = d[mobj + 2], d[mobj + 3]
    sprites = ptrs.get(mobj + 4)
    palettes = ptrs.get(mobj + 0x2C)
    imgs = pointer_run(ptrs, sprites)
    pals = pointer_run(ptrs, palettes)
    return dict(fmt=fmt, siz=siz, flags=u16(d, mobj + 0x30), images=imgs, palettes=pals,
                prim=tuple(d[mobj + 0x50:mobj + 0x54]))


def model_lights(f):
    """The model's light colors, set at the start of its display lists (DB0A0000 = light 1:
    the directional light, DB0A0018 = light 2: ambient, i.e. the shadow color)."""
    out = {}
    first = f.details.get('hi') or next(iter(f.details.values()))     # stages: their first layer
    for j in first['joints']:
        if j.dl is None:
            continue
        try:
            candidates = linked_display_lists(f.char, f.char_int, j.dl)
        except BadDL:
            continue
        o = next((dl for dl in candidates if f.char[dl] != 0xDF), None)
        if o is not None:
            break
    else:
        o = None
    while o is not None and o + 8 <= len(f.char) and f.char[o] not in (0x01, 0x05, 0x06, 0xDE, 0xDF, 0xFD):
        w0, w1 = u32(f.char, o), u32(f.char, o + 4)
        if w0 == 0xDB0A0000:
            out['light'] = f'{w1 >> 8:06X}'
        elif w0 == 0xDB0A0018:
            out['shadow'] = f'{w1 >> 8:06X}'
        o += 8
    return out


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    c = min(max(c, 0.0), 1.0)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
