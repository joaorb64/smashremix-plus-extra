"""A fighter's shield pose (decomp ftcommonguard1.c, FTAttributes 0x2D8..0x2F8): how the body
leans while shielding, for every stick direction.

The shield-pose file holds:
  dobj_lookup (attr + 0x2D8)            a DObjDesc array: each joint's neutral shield pose
  shield_anim_joints[8] (attr + 0x2DC)  per 45-degree sector (0 = forward, counter-clockwise:
                                        1 = forward-up ... 7 = down-forward) an array of joint
                                        scripts (AObjEvent32, like the stages' anim_joints),
                                        played at the stick angle within the sector (0..45)
Both are indexed in the order lbCommonAddDObjAnimJointAll walks the fighter's joint tree from
XRotN while it guards: XRotN, its children depth first. The joints that exist then are the body
joints setup_parts creates (attr + 0x29C) plus the hidden parts (attr + 0x2D0) the guard
animations turn on (their motion flags, 0xA0000000 in every vanilla fighter: hidden parts 0 and
2, XRotN and YRotN). YRotN, the last child of XRotN, is where the shield sits.

Each frame, every joint with a script gets  neutral + (sector pose - neutral) * stick range
(range 0..1: how far the stick is pushed; rotations as Euler angles). A joint whose script is
NULL keeps the pose the guard animation left it in.

Here, the angle is a single timeline: frame = stick angle in degrees, 0..360 (sector i covers
frames 45 i .. 45 i + 45).
"""
import struct

import numpy as np

import model_files as MF
import jointanim as JA

A_SHIELD_SIZE = 0x74             # f32 shield_size: the bubble's radius at full health (a 30-unit
                                  # sphere on YRotN, scaled size / 30)
A_SETUP_PARTS = 0x29C
A_HIDDENPARTS = 0x2D0
A_LOOKUP = 0x2D8
A_SECTORS = 0x2DC
N_SECTORS = 8
SECTOR_FRAMES = 45
GUARD_FLAGS = 0xA0000000          # the guard animations' hidden parts (motion flags' top bits)
ROOT_NAMES = {1: 'TransN', 2: 'XRotN', 3: 'YRotN'}      # fp->joints[1..3] (hidden parts)
HIDDEN_MAX = 32


def joint_name(game_joint):
    """fp->joints index -> the Blender bone name (body joints: joint_NN, NN = DObjDesc index)."""
    if game_joint in ROOT_NAMES:
        return ROOT_NAMES[game_joint]
    return f'joint_{game_joint - MF.JOINT_COMMON_START:02d}'


def hidden_parts(f):
    """attr->hiddenparts: [(root_joint_id, parent_joint_id, partindex, joint_kind)]. The table
    has no stored length: it runs while the entries look like ones."""
    hp = f.ptr_main(f.attr + A_HIDDENPARTS)
    if hp is None or hp[0] != 'main':
        return []
    out = []
    for k in range(HIDDEN_MAX):
        o = hp[1] + 16 * k
        if o + 16 > len(f.main):
            break
        root, parent, part, kind = struct.unpack_from('>4i', f.main, o)
        if not (0 < root < 40 and 0 <= parent < 40 and 0 <= kind <= 3):
            break
        out.append((root, parent, part, kind))
    return out


def guard_tree(f, flags=GUARD_FLAGS):
    """The fighter's joint tree while it guards: {fp->joints index: [children]} (in order), as
    ftManagerMakeFighter (lbCommonSetupFighterPartsDObjs: the setup_parts joints, each the last
    child of the latest joint one level up) and ftMainAddHiddenPartID (the hidden parts whose
    bit is set in the motion flags) build it."""
    joints = f.details['hi']['joints']
    sp = f.ptr_main(f.attr + A_SETUP_PARTS)
    words = struct.unpack_from('>2I', f.main, sp[1]) if sp and sp[0] == 'main' else (0xFFFFFFFF, 0xFFFFFFFF)
    kids = {0: []}
    last_at = {}
    for i, j in enumerate(joints):
        w = words[0] if i < 32 else words[1]
        if not (w >> (31 - i % 32)) & 1:
            continue
        g = i + MF.JOINT_COMMON_START
        parent = 0 if j.depth == 0 else last_at.get(j.depth - 1)
        if parent is None:
            continue
        kids.setdefault(parent, []).append(g)
        kids[g] = []
        last_at[j.depth] = g
    for i, (root, parent, _, kind) in enumerate(hidden_parts(f)):
        if i >= 32 or not (flags >> (31 - i)) & 1 or parent not in kids or root in kids:
            continue
        ch = kids[parent]
        if kind == 0:                       # its parent's last child
            ch.append(root)
            kids[root] = []
        elif kind == 1:                     # its parent's first child
            ch.insert(0, root)
            kids[root] = []
        elif kind == 2:                     # after its parent's first child
            ch.insert(1 if ch else 0, root)
            kids[root] = []
        else:                               # between the parent and its children
            kids[root] = list(ch)
            kids[parent] = [root]
    return kids


def walk(kids, root):
    """lbCommonGetTreeDObjNextFromRoot's order: root, then its descendants depth first."""
    out, stack = [], [root]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(kids.get(n, [])))
    return out


def read_lookup(d, off):
    """The neutral pose (a DObjDesc array): [dict(depth, t, r, s)] up to its end marker."""
    out = []
    while off + MF.DOBJDESC_SIZE <= len(d) and MF.u32(d, off) != MF.DOBJDESC_END and len(out) < 64:
        v = struct.unpack_from('>9f', d, off + 8)
        out.append(dict(depth=MF.u32(d, off), t=np.array(v[:3]), r=np.array(v[3:6]), s=np.array(v[6:])))
        off += MF.DOBJDESC_SIZE
    return out


def values_of(e):
    """A neutral entry as track values (jointanim.NAMES indices: 0..2 rot, 4..6 tra, 7..9 sca)."""
    return {0: e['r'][0], 1: e['r'][1], 2: e['r'][2], 4: e['t'][0], 5: e['t'][1], 6: e['t'][2],
            7: e['s'][0], 8: e['s'][1], 9: e['s'][2]}


def load(f, shield, int_head):
    """The shield pose of fighter f (model_files.load) from its shield-pose file's bytes.
    main.bin's 0x2D8..0x2F8 pointers are external: their offsets point into this file.
    -> dict(order, names, lookup, sectors, poses, int_head, ...)."""
    ptrs = MF.chain(shield, int_head)
    look = f.main_ext.get(f.attr + A_LOOKUP)
    secs = [f.main_ext.get(f.attr + A_SECTORS + 4 * k) for k in range(N_SECTORS)]
    if look is None or None in secs:
        raise ValueError('main.bin has no shield pose pointers (0x2D8..0x2F8 not external)')
    lookup = read_lookup(shield, look)
    kids = guard_tree(f)
    if 2 not in kids:
        raise ValueError('no XRotN while guarding (hidden parts / motion flags)')
    order = walk(kids, 2)
    if len(order) != len(lookup):
        print(f'  note: shield pose has {len(lookup)} joints, the guard tree {len(order)}: '
              f'matched in order, the extra ones left out')
    n = min(len(order), len(lookup))
    order, lookup = order[:n], lookup[:n]
    sectors, poses = [], []
    total = SECTOR_FRAMES * N_SECTORS
    keys = [{} for _ in range(n)]                 # per joint {track: frames on the 0..360 timeline}
    for k, arr in enumerate(secs):
        scripts = [ptrs.get(arr + 4 * i) for i in range(n)]
        tracks = [set() for _ in range(n)]          # the tracks each script sets
        sectors.append(dict(field=arr, scripts=scripts, tracks=tracks))
        # every joint's values per frame 0..45 (a joint without a script: its neutral pose)
        per = []
        for i, sc in enumerate(scripts):
            base = values_of(lookup[i])
            if sc is None:
                per.append([dict(base) for _ in range(SECTOR_FRAMES + 1)])
                continue
            ks = {}
            out, _ = MF.simulate_anim(shield, ptrs, sc, SECTOR_FRAMES + 1, ks)
            tracks[i] = {t for t in ks if t != 3}
            for t, fr in ks.items():
                keys[i].setdefault(t, set()).update(SECTOR_FRAMES * k + x for x in fr if x <= SECTOR_FRAMES)
            seq, cur = [], dict(base)
            for fr in out:
                cur = dict(cur)
                cur.update({t: v for t, v in fr.items() if t != 3})
                seq.append(cur)
            per.append(seq)
        poses.append(per)
    # the game's key frames per joint on the 0..360 timeline: the sector starts, and where
    # its scripts set values (e.g. 44: a sector's last key before the next one's)
    starts = [SECTOR_FRAMES * k for k in range(N_SECTORS + 1)]
    keys = [JA.group_keys(ks, total, starts) for ks in keys]
    return dict(order=order, names=[joint_name(g) for g in order], lookup=lookup, sectors=sectors, keys=keys,
                poses=poses, int_head=int_head, lookup_off=look, size=len(shield), flags=GUARD_FLAGS)


def timeline(sp):
    """The whole turn as one timeline: [per joint [values per frame 0..360]] (frame = stick
    angle in degrees; a sector boundary takes the next sector's first frame, 360 the last
    sector's end)."""
    n = len(sp['order'])
    out = [[] for _ in range(n)]
    for k in range(N_SECTORS):
        last = k == N_SECTORS - 1
        for i in range(n):
            out[i].extend(sp['poses'][k][i][:SECTOR_FRAMES + (1 if last else 0)])
    return out


# ---------------------------------------------------------------------------- writing

# a pose this close to the file's counts as unedited: the file's own values are written back
# for it. (A bone edited in Blender: its curves between keys can be a few degrees from the
# game's, so a sector of it counts as edited past ~3 degrees; the bones not edited (their
# keys as built) are never compared.)
SAME_TOL = {'rot': 0.05, 'tra': 0.5, 'sca': 0.004}


def _same_pose(a, b):
    """Two joint poses ({track: value}) the same transform within SAME_TOL (rotations compared
    as matrices: one turn has more than one set of Euler angles)."""
    if any(abs(a[k] - b[k]) > SAME_TOL['tra'] for k in ('tx', 'ty', 'tz')):
        return False
    if any(abs(a[k] - b[k]) > SAME_TOL['sca'] for k in ('sx', 'sy', 'sz')):
        return False
    Ra = MF.rot_zyx(a['rx'], a['ry'], a['rz'])
    Rb = MF.rot_zyx(b['rx'], b['ry'], b['rz'])
    return float(np.abs(Ra - Rb).max()) <= SAME_TOL['rot']


def _named(v):
    """{track index: value} -> {track name: value}."""
    return {JA.NAMES[i]: float(x) for i, x in v.items() if i != 3}


def _decompose_seq(mats, start):
    """Local 4x4 matrices (flat, row-major) -> [{track: value}] with Euler angles continuous
    from START (the file's values: the same turn of the angles the game lerps)."""
    out, prev = [], start
    for m in mats:
        prev = JA.decompose(np.array(m, float).reshape(4, 4), prev)
        out.append(prev)
    return out


def script_bytes(d, off):
    """An AObjEvent32 script's bytes from off through its end command (model_files.simulate_anim's
    commands), or None when it jumps (its pointers would need moving) or isn't one."""
    o = off
    while o + 4 <= len(d):
        w = MF.u32(d, o)
        op, n = w >> 25, bin((w >> 15) & 0x3FF).count('1')
        if op == 0:
            return d[off:o + 4]
        if op in (3, 4, 10, 11, 7, 17):
            o += 4 + 4 * n
        elif op in (5, 6):
            o += 4 + 8 * n
        elif op in (8, 9):
            o += 4 + 4 * n
        elif op in (2, 12, 15, 16):
            o += 4
        elif op == 13:
            o += 12
        else:
            return None
    return None


def build_file(sp, blend, data=None):
    """The shield pose edited in Blender (blend_to_gltf.shield_pose: local game-space matrices
    per bone) over the file's (load) -> a new shield-pose file, or None when nothing changed.
    -> dict(data, int_head, lookup, sectors [8 array offsets], changed [what changed]).

    Where Blender's values stay within SAME_TOL of the file's, the file's own values are kept
    (no drift from the .blend's curves); a joint the file gave no script in a sector gets one
    only if Blender moves it away from its neutral pose there. Layout: the neutral DObjDesc
    array (+ its end marker), the 8 sector arrays, the scripts (AObjEvent32: the file's own, given its bytes as data, where unedited;
    jointanim.build)."""
    n = len(sp['order'])
    changed = []
    ed = blend.get('edited')
    edited_turn = None if ed is None else {b for t, b in ed if t == 'turn'}
    edited_neutral = None if ed is None else {b for t, b in ed if t == 'neutral'}
    neutral, seq = [], []                 # per joint: {name: value}; [per frame {name: value}]
    edited = set()                        # (sector, joint) Blender changed
    for i, nm in enumerate(sp['names']):
        old_n = _named(values_of(sp['lookup'][i]))
        if nm in blend['neutral'] and (edited_neutral is None or nm in edited_neutral):
            new_n = JA.decompose(np.array(blend['neutral'][nm], float).reshape(4, 4), old_n)
            new_n = {k: new_n[k] for k in JA.EDITABLE}
        else:
            new_n = dict(old_n)
        if _same_pose(new_n, old_n):
            new_n = old_n
        else:
            changed.append(f'{nm} neutral')
        neutral.append(new_n)
    for i, nm in enumerate(sp['names']):
        per = []
        for k in range(N_SECTORS):
            had = sp['sectors'][k]['scripts'][i] is not None
            # the file's values (a joint without a script: its neutral pose, the new one)
            old = [_named(v) for v in sp['poses'][k][i]] if had else \
                [dict(neutral[i]) for _ in range(SECTOR_FRAMES + 1)]
            ref = old if had else [_named(values_of(sp['lookup'][i]))] * (SECTOR_FRAMES + 1)
            mats = blend['turn'].get(nm)
            if edited_turn is not None and nm not in edited_turn:
                mats = None                                   # (not edited)
            if mats is None or len(mats) <= SECTOR_FRAMES * k + SECTOR_FRAMES:
                per.append(old)
                continue
            new = _decompose_seq(mats[SECTOR_FRAMES * k:SECTOR_FRAMES * k + SECTOR_FRAMES + 1], ref[0])
            if all(_same_pose(a, b) for a, b in zip(new[:SECTOR_FRAMES], ref)):   # (45: never played)
                per.append(old)
            else:
                changed.append(f'{nm} sector {k} ({45 * k}-{45 * k + 45} deg)')
                edited.add((k, i))
                per.append([{t: v[t] for t in JA.EDITABLE} for v in new])
        seq.append(per)
    if not changed:
        return None

    # ---- layout
    out = bytearray()
    fields = []                                       # (field offset, target offset)
    for i in range(n):
        e = neutral[i]
        out += struct.pack('>2I9f', sp['lookup'][i]['depth'], 0, e['tx'], e['ty'], e['tz'],
                           e['rx'], e['ry'], e['rz'], e['sx'], e['sy'], e['sz'])
    out += struct.pack('>I', MF.DOBJDESC_END) + bytes(MF.DOBJDESC_SIZE - 4)
    arrays = []
    for k in range(N_SECTORS):
        arrays.append(len(out))
        out += bytes(4 * n)
    notes = []
    for k in range(N_SECTORS):
        for i in range(n):
            vals = seq[i][k]
            had = sp['sectors'][k]['scripts'][i] is not None
            moves = any(not _same_pose(v, neutral[i]) for v in vals[:SECTOR_FRAMES])
            if not had and not moves:
                continue
            # unedited: the file's own script, as it is (when it has no jumps); its neutral
            # pose changing doesn't change what it plays
            old_sc = sp['sectors'][k]['scripts'][i] if had and data is not None and (k, i) not in edited else None
            raw = script_bytes(data, old_sc) if old_sc is not None else None
            if raw is not None:
                while len(out) % 4:
                    out += b'\0'
                fields.append((arrays[k] + 4 * i, len(out)))
                out += raw
                continue
            keep = [JA.NAMES[t] for t in sp['sectors'][k]['tracks'][i]] if had else []
            anim = dict(frames=SECTOR_FRAMES, loop=False, loop_start=0,
                        samples={t: [v[t] for v in vals] for t in JA.EDITABLE})
            script, fixups, bad = JA.build(anim, neutral[i], keep_tracks=keep)
            if script is None:
                continue
            if bad:
                notes.append(f'{sp["names"][i]} sector {k}: frame by frame ({", ".join(bad)})')
            while len(out) % 4:
                out += b'\0'
            o = len(out)
            out += script
            fields += [(o + fld, o + tgt) for fld, tgt in fixups]
            fields.append((arrays[k] + 4 * i, o))
            if not had:
                notes.append(f'{sp["names"][i]} sector {k}: new script (it moved away from its neutral pose)')
    while len(out) % 4:
        out += b'\0'
    if len(out) > 0x3FFFC:
        raise SystemExit(f'shield pose file is {len(out)} bytes: over the 0x3FFFC limit')
    fields.sort()
    for a, (fld, tgt) in enumerate(fields):
        nxt = fields[a + 1][0] // 4 if a + 1 < len(fields) else 0xFFFF
        struct.pack_into('>HH', out, fld, nxt, tgt // 4)
    head = fields[0][0] if fields else 0x3FFFC
    return dict(data=bytes(out), int_head=head, lookup=0, sectors=arrays, changed=changed, notes=notes)
