"""Fighter attributes that point at joints (decomp fttypes.h FTAttributes), edited in Blender on
the bones (Bone > N64 Joint, Armature > N64 Fighter Joints).

Joint ids are fp->joints indices ("game joints"): 0 TopN, 1 TransN, 2 XRotN, 3 YRotN, then
DObjDesc entry i = game joint i + 4 (bone joint_NN, NN = i). At most 37 (FTPARTS_JOINT_NUM_MAX).

  + 0x29C u32 *setup_parts        two words, bit 31 - (i % 32) of word i // 32: DObjDesc entry i
                                  becomes a joint when the fighter is made (off: withheld - only a
                                  hidden part adds it, or it never exists)
  + 0x2A0 u32 *animlock           same layout: joints the game stops animating with motion flag 0x1
                                  (it keeps their matrix as it was; ftParamSetAnimLocks)
  + 0x2A4 s32 effect_joint_ids[5] joints the game cycles through for some effects (electric /
                                  flame hits: ftParamGetEffectJointPosition)
  + 0x2D0 FTHiddenPart *          {s32 root, s32 parent, s32 init, s32 kind}[]: motion flag bit
                                  31 - i adds part i's root joint under parent (kind 0 last child,
                                  1 first child, 2 after the first child, 3 between the parent and
                                  its children); init != 0: the joint gets a transform kind 0x4B.
                                  No stored length (the table ends at an entry that doesn't look
                                  like one; a zero entry ends it)
  + 0x2FC s32 joint_rfoot_id, f32 rotate; + 0x304 left: slope foot IK (legs.py)
  + 0x324 Vec3f *translate_scales one per game joint (37): its animation's translation is scaled
                                  by it (unless motion flag 0x4); NULL in every Remix fighter
  + 0x32C FTAccessPart *          {s32 joint, dl, mobjsubs, scripts}: an accessory drawn on a joint
  + 0x330 FTTexturePart[2]        {u8 joint, u8 mobj index hi, lo}: facial expressions
  + 0x334 s32 joint_itemheavy_id, + 0x33C s32 joint_itemlight_id

joint_attrs(f) -> the manifest's 'joint_attrs'; plan(f, main, new, orig) -> what the import writes.
"""
import struct

import model_files as MF

A_SETUP = 0x29C
A_ANIMLOCK = 0x2A0
A_EFFECT = 0x2A4
N_EFFECT = 5
A_HIDDEN = 0x2D0
A_RFOOT_ID, A_RFOOT_LEN, A_LFOOT_ID, A_LFOOT_LEN = 0x2FC, 0x300, 0x304, 0x308
A_TSCALES = 0x324
A_ACCESS = 0x32C
A_TEXPARTS = 0x330
A_ITEMHEAVY, A_ITEMLIGHT = 0x334, 0x33C
JOINT_MAX = 37
HIDDEN_MAX = 32
KINDS = ('last child', 'first child', 'after first child', 'between parent and children')


def s32(d, o):
    return struct.unpack_from('>i', d, o)[0]


def _words(f, fld, main=None):
    p = f.ptr_main(f.attr + fld)
    if p is None:
        return None, None
    d = f.main if p[0] == 'main' else f.char
    return list(struct.unpack_from('>2I', d, p[1])), p


def read_hidden(d, off):
    out = []
    for k in range(HIDDEN_MAX):
        o = off + 16 * k
        if o + 16 > len(d):
            break
        root, parent, init, kind = struct.unpack_from('>4i', d, o)
        if not (0 < root < 40 and 0 <= parent < 40 and 0 <= kind <= 3):
            break
        out.append([root, parent, init, kind])
    return out


def joint_attrs(f):
    """Everything above as plain data (game joint ids); None where the fighter has no table."""
    m, a = f.main, f.attr
    setup, _ = _words(f, A_SETUP)
    animlock, _ = _words(f, A_ANIMLOCK)
    hp = f.ptr_main(a + A_HIDDEN)
    hidden = read_hidden(f.main if hp[0] == 'main' else f.char, hp[1]) if hp else []
    ts = f.ptr_main(a + A_TSCALES)
    tscales = None
    if ts is not None:
        d = f.main if ts[0] == 'main' else f.char
        tscales = [list(struct.unpack_from('>3f', d, ts[1] + 12 * i)) for i in range(JOINT_MAX)]
    tp = f.ptr_main(a + A_TEXPARTS)
    texparts = None
    if tp is not None and tp[0] == 'main':
        texparts = [list(m[tp[1] + 3 * k:tp[1] + 3 * k + 3]) for k in range(2)]
    ap = f.ptr_main(a + A_ACCESS)
    access = s32(f.main if ap[0] == 'main' else f.char, ap[1]) if ap else None
    return dict(
        joints=len(f.details['hi']['joints']),
        setup=setup, animlock=animlock,
        effect=[s32(m, a + A_EFFECT + 4 * i) for i in range(N_EFFECT)],
        hidden=hidden,
        foot=dict(r=s32(m, a + A_RFOOT_ID), r_len=MF.f32(m, a + A_RFOOT_LEN),
                  l=s32(m, a + A_LFOOT_ID), l_len=MF.f32(m, a + A_LFOOT_LEN)),
        item_heavy=s32(m, a + A_ITEMHEAVY), item_light=s32(m, a + A_ITEMLIGHT),
        translate_scales=tscales, texture_parts=texparts, access_joint=access)


def bit(words, i):
    """DObjDesc entry i's bit in a setup_parts / animlock pair."""
    return bool((words[i // 32] >> (31 - i % 32)) & 1)


def words_of(bits):
    """[bool per DObjDesc entry] -> the two words."""
    w = [0, 0]
    for i, on in enumerate(bits):
        if on:
            w[i // 32] |= 1 << (31 - i % 32)
    return w


class Plan:
    """What the import does to main.bin / character.bin:
    writes   [(main.bin offset, bytes)]  in place
    unlink   [main.bin field]            internal pointers to drop (their data moves to character.bin)
    new      {main.bin field: bytes}     data appended to character.bin, the field pointed at it
    notes    [str]"""
    def __init__(self):
        self.writes, self.unlink, self.new, self.notes = [], [], {}, []

    def __bool__(self):
        return bool(self.writes or self.unlink or self.new)


def plan(f, new, orig):
    """new / orig: joint_attrs dicts (Blender's, the export's) -> Plan. Only what changed."""
    P = Plan()
    a, m = f.attr, f.main
    nj = orig.get('joints') or len(f.details['hi']['joints'])

    def valid(g, what, allow_none=False):
        if g is None and allow_none:
            return True
        if not isinstance(g, int) or not 0 <= g < min(JOINT_MAX, nj + 4):
            raise SystemExit(f'joint attributes: {what}: joint {g} is not one of the fighter\'s')
        return True

    def put(fld, fmt, *vals):
        P.writes.append((fld, struct.pack(fmt, *vals)))

    def array(fld, data, what, in_place_ok=True, old_len=None):
        """data for the table at attr + fld: in place when it fits where it is, else a new copy
        in character.bin (main.bin's internal pointer dropped)."""
        p = f.ptr_main(a + fld)
        if p is not None and p[0] == 'main' and in_place_ok and (old_len is None or len(data) <= old_len):
            P.writes.append((p[1], data))
        elif p is not None and p[0] == 'char':
            P.new[a + fld] = data                   # (an external pointer: re-pointed)
        else:
            if p is not None:
                P.unlink.append(a + fld)
            P.new[a + fld] = data
            P.notes.append(f'{what}: written to character.bin (new pointer)')

    for key, fld in (('setup', A_SETUP), ('animlock', A_ANIMLOCK)):
        if new.get(key) is not None and new[key] != orig.get(key):
            array(fld, struct.pack('>2I', *new[key]), key)
            P.notes.append(f'{key}: {orig.get(key)} -> {new[key]}' if orig.get(key) else f'{key}: {new[key]}')
    if new.get('effect') is not None and new['effect'] != orig.get('effect'):
        for i, g in enumerate(new['effect']):
            valid(g, f'effect joint {i}')
            put(a + A_EFFECT + 4 * i, '>i', g)
        P.notes.append(f"effect joints: {orig.get('effect')} -> {new['effect']}")
    for key, fld in (('item_heavy', A_ITEMHEAVY), ('item_light', A_ITEMLIGHT)):
        if new.get(key) is not None and new[key] != orig.get(key):
            valid(new[key], key)
            put(a + fld, '>i', new[key])
            P.notes.append(f'{key}: {orig.get(key)} -> {new[key]}')
    nf, of = new.get('foot') or {}, orig.get('foot') or {}
    for side, fid, flen in (('r', A_RFOOT_ID, A_RFOOT_LEN), ('l', A_LFOOT_ID, A_LFOOT_LEN)):
        if side in nf and nf[side] != of.get(side):
            valid(nf[side], f'foot {side}')
            put(a + fid, '>i', nf[side])
            P.notes.append(f'foot {side}: joint {of.get(side)} -> {nf[side]}')
        if f'{side}_len' in nf and abs(nf[f'{side}_len'] - of.get(f'{side}_len', 0)) > 1e-4:
            put(a + flen, '>f', nf[f'{side}_len'])
            P.notes.append(f"foot {side} length: {of.get(f'{side}_len', 0):g} -> {nf[f'{side}_len']:g}")
    if new.get('hidden') is not None and new['hidden'] != orig.get('hidden'):
        hs = new['hidden']
        if len(hs) > HIDDEN_MAX:
            raise SystemExit(f'{len(hs)} hidden parts: the motion flags have room for {HIDDEN_MAX}')
        for i, (root, parent, init, kind) in enumerate(hs):
            valid(root, f'hidden part {i} root')
            valid(parent, f'hidden part {i} parent')
            if root < 1 or not 0 <= kind <= 3:
                raise SystemExit(f'hidden part {i}: root {root} / kind {kind} out of range')
        data = b''.join(struct.pack('>4i', *h) for h in hs)
        old = len(orig.get('hidden') or []) * 16
        # a shorter table ends with a zero entry (over the old last ones); a longer one moves to
        # character.bin, ended by one
        array(A_HIDDEN, data if len(data) == old else data + b'\0' * 16, 'hidden parts', old_len=old)
        P.notes.append(f"hidden parts: {len(orig.get('hidden') or [])} -> {len(hs)}")
    nts, ots = new.get('translate_scales'), orig.get('translate_scales')
    if nts is not None:
        base = ots or [[1.0, 1.0, 1.0]] * JOINT_MAX
        if any(abs(x - y) > 1e-5 for u, v in zip(nts, base) for x, y in zip(u, v)):
            nts = (list(nts) + base[len(nts):])[:JOINT_MAX]
            array(A_TSCALES, b''.join(struct.pack('>3f', *v) for v in nts), 'translate scales')
            P.notes.append('translate scales: ' + ', '.join(
                f'joint {g} {tuple(round(x, 4) for x in v)}' for g, v in enumerate(nts) if v != base[g]))
    if new.get('texture_parts') is not None and orig.get('texture_parts') is not None \
            and new['texture_parts'] != orig['texture_parts']:
        p = f.ptr_main(a + A_TEXPARTS)
        for k, (g, hi, lo) in enumerate(new['texture_parts']):
            if not (0 <= g < 256 and 0 <= hi < 256 and 0 <= lo < 256):
                raise SystemExit(f'texture part {k}: values out of range')
            P.writes.append((p[1] + 3 * k, bytes((g, hi, lo))))
        P.notes.append(f"texture parts: {orig['texture_parts']} -> {new['texture_parts']}")
    if new.get('access_joint') is not None and orig.get('access_joint') is not None \
            and new['access_joint'] != orig['access_joint']:
        valid(new['access_joint'], 'accessory part')
        p = f.ptr_main(a + A_ACCESS)
        if p[0] == 'main':
            P.writes.append((p[1], struct.pack('>i', new['access_joint'])))
            P.notes.append(f"accessory part joint: {orig['access_joint']} -> {new['access_joint']}")
        else:
            P.notes.append('accessory part joint: its table is in character.bin, not changed')
    return P


def unlink_internal(main, head, fld):
    """Drop main.bin's internal pointer at fld from its relocation chain (head: the chain's
    first field) and zero the field. -> the chain's head (the next pointer when fld was the
    head: config.yaml offsets main[0] must follow; 0x3FFFC: none left)."""
    chain = list(MF.chain(bytes(main), head).keys())
    if fld not in chain:
        return head
    i = chain.index(fld)
    if i == 0:
        head = chain[1] if len(chain) > 1 else 0x3FFFC
    else:
        struct.pack_into('>H', main, chain[i - 1], struct.unpack_from('>H', main, fld)[0])   # prev -> fld's next
    struct.pack_into('>I', main, fld, 0)
    return head
