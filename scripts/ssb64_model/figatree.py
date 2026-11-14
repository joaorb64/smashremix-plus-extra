"""Fighter animations (decomp ftanim.c, ftmain.c ftMainSetStatus, lbcommon.c).

An animation file ("figatree") is an array of pointers, one per joint in the order
lbCommonAddFighterPartsFigatree walks the fighter's joint tree from TopN's first child (NULL:
the joint isn't animated, it stays at its rest), then the scripts. A script is 16-bit commands
(AObjEvent16: opcode 5 bits, track flags 10 bits, a "duration follows" bit), with each value a
fixed-point s16: rotations / 512 (radians), translations / 4, scales / 4096; rates / 512, / 32,
/ 8192. Motions flagged is_anim_joint (0x8) use 32-bit scripts (AObjEvent32) instead, like the
stages' anim_joints.

Which joints the tree has, so the array's order, depends on the motion's flags (FTMotionDesc
anim_desc): bit 31 - i turns hidden part i on (attr->hiddenparts: TransN, XRotN, YRotN, extra
joints like Samus's or Kirby's), on top of the joints setup_parts makes. The low bits:
0x10 submotion script, 0x8 32-bit scripts, 0x4 no translate scales, 0x2 the shield-pose file,
0x1 animation locks. TransN, when on, is the root (re-inserted above everything every time); it
carries the root motion (the game moves the fighter by its translation, not the body).
"""
import struct

import model_files as MF
import jointanim as JA
import shieldpose as SP

FLAG_SUBMOTION = 0x10
FLAG_ANIMJOINT = 0x8
FLAG_NO_TRANSLATE_SCALES = 0x4
FLAG_SHIELDPOSE = 0x2
FLAG_ANIMLOCKS = 0x1
HIDDEN_MASK = 0xFFFFFFE0

# AObjEvent16 opcodes
END, WAIT, LIN_BLOCK, LIN, CUB_BLOCK, CUB, RATE, CUB0_BLOCK, CUB0, STEP_BLOCK, STEP, LENGTH, TRAI, \
    LOOP, SETFLAGS = range(15)
VALUE_SCALE = {0: 1 / 512, 1: 1 / 4, 2: 1 / 4096, 3: 1 / 16384 - 3e-12}
RATE_SCALE = {0: 1 / 512, 1: 1 / 32, 2: 1 / 8192, 3: 1 / 16384 - 3e-12}
TRACKS = 10


def _kind(track):
    return 0 if track < 3 else 3 if track == 3 else 1 if track < 7 else 2


def s16(d, o):
    return struct.unpack_from('>h', d, o)[0]


def u16(d, o):
    return struct.unpack_from('>H', d, o)[0]


def simulate16(d, script, frames, keys=None, info=None):
    """ftAnimParseDObjFigatree + gcPlayDObjAnimJoint for `frames` frames: [{track: value}] per
    frame, and the frame the script first loops back or ends (None: it runs longer). keys gets
    {track: set of frames} where the script sets each track's value; info gets looped=True when
    the script loops (a Loop command)."""
    tracks, out = {}, []
    wait, frame, ptr, seen, loop_at = 'changed', 0.0, script, {script}, None
    ended = False

    def track(i):
        return tracks.setdefault(i, dict(kind=None, vb=0.0, vt=0.0, rb=0.0, rt=0.0, len=0.0, li=1.0))

    for n in range(frames):
        if not ended:
            if wait == 'changed':
                wait = -frame
            else:
                wait -= 1.0
                frame += 1.0
            while not ended and wait <= 0.0:
                if ptr is None or ptr + 2 > len(d):
                    op = END
                else:
                    w = u16(d, ptr)
                    op, flags, toggle = w >> 11, (w >> 1) & 0x3FF, w & 1
                    ptr += 2
                if op == END:
                    for a in tracks.values():
                        if a['kind']:
                            a['len'] += 1.0 + wait
                    ended = True
                    loop_at = loop_at if loop_at is not None else n
                    break
                payload = 0.0
                if op in (LIN_BLOCK, LIN, CUB_BLOCK, CUB, RATE, CUB0_BLOCK, CUB0, STEP_BLOCK, STEP, LENGTH,
                          WAIT, SETFLAGS):
                    if toggle:
                        payload = float(u16(d, ptr))
                        ptr += 2
                if op in (LIN_BLOCK, LIN, CUB_BLOCK, CUB, CUB0_BLOCK, CUB0, STEP_BLOCK, STEP, RATE, LENGTH):
                    for i in range(TRACKS):
                        if not flags >> i:
                            break
                        if not (flags >> i) & 1:
                            continue
                        a = track(i)
                        k = _kind(i)
                        if op == LENGTH:
                            a['len'] += payload
                            continue
                        if op == RATE:
                            a['rt'] = s16(d, ptr) * RATE_SCALE[k]
                            ptr += 2
                            continue
                        if keys is not None:
                            keys.setdefault(i, set()).add(n)
                        a['vb'], a['vt'] = a['vt'], s16(d, ptr) * VALUE_SCALE[k]
                        ptr += 2
                        if op in (CUB_BLOCK, CUB):
                            a['rb'], a['rt'] = a['rt'], s16(d, ptr) * RATE_SCALE[k]
                            ptr += 2
                            a['kind'] = 'cubic'
                            if payload:
                                a['li'] = 1.0 / payload
                        elif op in (CUB0_BLOCK, CUB0):
                            a['rb'], a['rt'] = a['rt'], 0.0
                            a['kind'] = 'cubic'
                            if payload:
                                a['li'] = 1.0 / payload
                        elif op in (LIN_BLOCK, LIN):
                            a['kind'] = 'linear'
                            if payload:
                                a['rb'] = (a['vt'] - a['vb']) / payload
                            a['rt'] = 0.0
                        else:                                   # step: the value after `payload`
                            a['kind'] = 'step'
                            a['li'] = payload
                            a['rt'] = 0.0
                        a['len'] = -wait - 1.0
                    if op in (LIN_BLOCK, CUB_BLOCK, CUB0_BLOCK, STEP_BLOCK):
                        wait += payload
                elif op in (WAIT, SETFLAGS):
                    wait += payload
                elif op == LOOP:
                    # the offset (bytes, from itself) back to the loop start
                    ptr = ptr + int(s16(d, ptr) / 2) * 2
                    if info is not None:
                        info['looped'] = True
                    if loop_at is None:
                        loop_at = n
                    seen.add(ptr)
                    frame = -wait
                elif op == TRAI:
                    ptr += 4                                    # (the interpolation path: not shown)
                else:                                           # not a joint command: stop
                    ended = True
                    loop_at = loop_at if loop_at is not None else n
        vals = {}
        for i, a in tracks.items():
            if a['kind']:
                if not ended:
                    a['len'] += 1.0
                if i != 3:
                    vals[i] = MF._anim_value(a)
        out.append(vals)
    return out, loop_at


def play(d, ptrs, script, rest, anim_joint=False, max_frames=3600):
    """A joint's script -> dict(frames (last frame), loop, samples {track name: per frame},
    keys {track: frames}) like jointanim.play; rest: {track name: value} for tracks it never
    sets (the joint's rest: the game resets joints before each animation)."""
    keys, info = {}, {}
    if anim_joint:
        out, end = MF.simulate_anim(d, ptrs, script, max_frames + 1, keys)
    else:
        out, end = simulate16(d, script, max_frames + 1, keys, info)
    n = end if end is not None else max_frames
    samples = {}
    for name in JA.EDITABLE:
        i = JA.NAMES.index(name)
        cur, seq = rest[name], []
        for fr in out[:n + 1]:
            cur = fr.get(i, cur)
            seq.append(float(cur))
        samples[name] = seq
    return dict(frames=n, samples=samples, keys={t: sorted(k for k in v if k <= n) for t, v in keys.items()},
                loop=bool(info.get('looped')))


def pointer_array(d, int_head):
    """The file's joint array: [script offset or None] up to where the first script starts."""
    ptrs = MF.chain(d, int_head)
    first = min(ptrs.values()) if ptrs else 0
    n = first // 4
    return [ptrs.get(4 * i) for i in range(n)], ptrs


def first_pointer_head(d):
    """The internal chain head of an animation file: its first non-zero word (the appender's
    rule: leading NULL entries aren't relocated)."""
    o = 0
    while o + 4 <= len(d) and MF.u32(d, o) == 0:
        o += 4
    return o


def joint_order(f, flags):
    """The fp->joints indices the animation's array covers, in order, for these motion flags."""
    kids = SP.guard_tree(f, flags & HIDDEN_MASK)
    top = kids.get(0) or []
    if not top:
        return []
    return SP.walk(kids, top[0])


def load(f, data, int_head=None, flags=0):
    """An animation file -> dict(names (bone per array entry), flags, frames, per bone
    {track name: per frame} values (the rest where it has no script), keys {bone: {group:
    frames}}) for fighter f (model_files.load)."""
    head = first_pointer_head(data) if int_head is None else int_head
    arr, ptrs = pointer_array(data, head)
    order = joint_order(f, flags)
    note = None
    if len(order) != len(arr):
        note = f'{len(arr)} entries, the flags {flags:08X} give {len(order)} joints'
    names = [SP.joint_name(g) for g in order[:len(arr)]]
    joints = {f'joint_{j.index:02d}': j for j in f.details['hi']['joints']}
    plays, frames = {}, 0
    for nm, sc in zip(names, arr):
        if sc is None:
            continue
        j = joints.get(nm)
        rest = JA.rest_values(j) if j is not None else dict(rx=0.0, ry=0.0, rz=0.0, tx=0.0, ty=0.0, tz=0.0,
                                                            sx=1.0, sy=1.0, sz=1.0)
        p = play(data, ptrs, sc, rest, anim_joint=bool(flags & FLAG_ANIMJOINT))
        plays[nm] = p
        frames = max(frames, p['frames'])
    values, keys = {}, {}
    for nm, p in plays.items():
        seq = p['samples']
        # every joint runs to the longest one's end (a shorter one holds its last value)
        values[nm] = {k: (v + [v[-1]] * (frames + 1 - len(v)))[:frames + 1] for k, v in seq.items()}
        keys[nm] = JA.group_keys({t: set(k) for t, k in p['keys'].items()}, frames)
    loops = {nm: True for nm, p in plays.items() if p.get('loop')}
    return dict(names=names, flags=flags, frames=frames + 1, values=values, keys=keys, note=note,
                entries=len(arr), loops=loops, loop=bool(loops))


GUESS_FLAGS = (0x0, 0x40000000, 0x80000000, 0xA0000000, 0xC0000000, 0xE0000000, 0x10000000, 0x50000000,
               0x20000000)


def rest_mismatch(f, data, flags):
    """How far the file's translations at frame 0 are from the joints' rest translations when
    read with these flags (per joint, capped at 100 units; the mean): low for the right joint
    order, high when the entries land on their neighbors."""
    arr, ptrs = pointer_array(data, first_pointer_head(data))
    joints = f.details['hi']['joints']
    zero = dict(rx=0.0, ry=0.0, rz=0.0, tx=0.0, ty=0.0, tz=0.0, sx=1.0, sy=1.0, sz=1.0)
    total, n = 0.0, 0
    for g, sc in zip(joint_order(f, flags), arr):
        if sc is None:
            continue
        rest = JA.rest_values(joints[g - MF.JOINT_COMMON_START]) if g >= MF.JOINT_COMMON_START else zero
        smp = play(data, ptrs, sc, rest, max_frames=2)['samples']
        d = sum((smp[k][0] - rest[k]) ** 2 for k in ('tx', 'ty', 'tz') if k in smp) ** 0.5
        total += min(d, 100.0)
        n += 1
    return total / max(n, 1)


def pick_flags(f, data, candidates):
    """The flags to read an animation file with -> (flags, guessed). Of its actions' flags
    (`candidates`), the ones whose joint tree it covers: as many entries as joints, or more
    (the game walks the tree's joints and never reads the rest: some files carry spare ones).
    Several: an exact one, unless one with spare entries sits clearly closer to the joints'
    rest translations (rest_mismatch under half; then the closest; ties: the first). None fits: the first one anyway (the game reads the file that way). No action
    plays it: guessed the same way from GUESS_FLAGS."""
    n = len(pointer_array(data, first_pointer_head(data))[0])

    def best(cands):
        fit = [c for c in cands if 0 < len(joint_order(f, c)) <= n]
        if len(fit) > 1:
            score = {c: rest_mismatch(f, data, c) for c in fit}
            exact = [c for c in fit if len(joint_order(f, c)) == n]
            spare = [c for c in fit if c not in exact]
            low = min(score[c] for c in spare) if spare else None
            if exact and (low is None or low >= 0.5 * min(score[c] for c in exact)):
                fit = exact
            elif spare:
                fit = [c for c in spare if score[c] <= low + 0.5]
        return fit[0] if fit else None
    cands = list(dict.fromkeys(c for c in candidates if c is not None))
    c = best(cands)
    if c is not None:
        return c, False
    if cands:
        return cands[0], False
    c = best(GUESS_FLAGS)
    return (c, True) if c is not None else (0, True)


# ---------------------------------------------------------------- writing
# How far a written animation may play from the motion it encodes (game units): rotations
# 0.004 rad (~0.23 degrees; the format's step is 1/512 rad), translations 0.5 (step 1/4),
# scales 0.002 (step 1/4096)
WRITE_TOL = {0: 0.004, 1: 0.5, 2: 0.002}


def _q(v, scale):
    return max(-32768, min(32767, int(round(v / scale))))


def _cmd(op, flags, payload=None):
    w = (op << 11) | ((flags & 0x3FF) << 1) | (1 if payload else 0)
    out = struct.pack('>H', w)
    if payload:
        out += struct.pack('>H', min(0xFFFF, int(payload)))
    return out


def encode_script(tracks, keys, rates, loop=False):
    """One joint's 16-bit script. tracks: {track: per-frame values}; keys: frames (first 0,
    last the animation's last frame); rates: {track: {key frame: rate per frame}}. A step to
    the first values, their rates, then per key a cubic (value, rate) reached over the frames to
    it; loop: back to the first cubic at the end (the game loops it), else End."""
    tr = sorted(tracks)
    flags = sum(1 << t for t in tr)
    vs = lambda t: VALUE_SCALE[_kind(t)]
    rs = lambda t: RATE_SCALE[_kind(t)]
    out = bytearray(_cmd(STEP, flags))
    for t in tr:
        out += struct.pack('>h', _q(tracks[t][keys[0]], vs(t)))
    out += _cmd(RATE, flags)
    for t in tr:
        out += struct.pack('>h', _q(rates[t][keys[0]], rs(t)))
    first = len(out)
    for a, b in zip(keys, keys[1:]):
        out += _cmd(CUB_BLOCK, flags, b - a)
        for t in tr:
            out += struct.pack('>hh', _q(tracks[t][b], vs(t)), _q(rates[t][b], rs(t)))
    if loop and len(keys) > 1:
        here = len(out) + 2                              # the offset is read from here
        out += _cmd(LOOP, 0) + struct.pack('>h', first - here)
    else:
        out += _cmd(END, 0)
    while len(out) % 4:
        out += b'\0'
    return bytes(out)


def _hermite_rows(n, keys):
    """For every frame 0..n-1: its segment's basis, as (row: frame, column: key) weights of the
    key values and of the key rates (per frame): the game's cubic (MF._anim_value) is Hermite."""
    import numpy as np
    K = len(keys)
    Hv, Hr = np.zeros((n, K)), np.zeros((n, K))
    for i, (a, b) in enumerate(zip(keys, keys[1:])):
        L = b - a
        u = (np.arange(a, b + 1) - a) / L
        u2, u3 = u * u, u * u * u
        Hv[a:b + 1, i] = 2 * u3 - 3 * u2 + 1
        Hv[a:b + 1, i + 1] = 3 * u2 - 2 * u3
        Hr[a:b + 1, i] = L * (u3 - 2 * u2 + u)
        Hr[a:b + 1, i + 1] = L * (u3 - u2)
    return Hv, Hr


def _fit_rates(V, keys):
    """Each key's rate (per frame), one per key shared by its two segments like the game's,
    that bring the cubics closest to the samples (least squares over every frame) ->
    (rates (tracks x keys), played values (tracks x frames))."""
    import numpy as np
    n = V.shape[1]
    Hv, Hr = _hermite_rows(n, keys)
    vals = V[:, keys]                                   # the key values: the samples there
    R = V.T - Hv @ vals.T                               # (frames, tracks)
    rates, *_ = np.linalg.lstsq(Hr, R, rcond=None)      # (keys, tracks)
    return rates.T, (Hv @ vals.T + Hr @ rates).T


def encode_linear(tracks, keys, loop=False):
    """A linear script: a step to the first values, then per key the values reached (in a
    straight line) over the frames to it; loop / End like encode_script."""
    tr = sorted(tracks)
    flags = sum(1 << t for t in tr)
    out = bytearray(_cmd(STEP, flags))
    for t in tr:
        out += struct.pack('>h', _q(tracks[t][keys[0]], VALUE_SCALE[_kind(t)]))
    first = len(out)
    for a, b in zip(keys, keys[1:]):
        out += _cmd(LIN_BLOCK, flags, b - a)
        for t in tr:
            out += struct.pack('>h', _q(tracks[t][b], VALUE_SCALE[_kind(t)]))
    if loop and len(keys) > 1:
        here = len(out) + 2
        out += _cmd(LOOP, 0) + struct.pack('>h', first - here)
    else:
        out += _cmd(END, 0)
    while len(out) % 4:
        out += b'\0'
    return bytes(out)


def _linear_keys(V, tol, cand):
    """Keys for straight segments through the samples, each frame within tol."""
    import numpy as np
    n = V.shape[1]
    keys, todo = {0, n - 1}, [(0, n - 1)]
    while todo:
        a, b = todo.pop()
        if b - a < 2:
            continue
        u = (np.arange(a, b + 1) - a) / (b - a)
        line = V[:, a:a + 1] + (V[:, b:b + 1] - V[:, a:a + 1]) * u
        err = (np.abs(line - V[:, a:b + 1]) / tol).max(0)
        at = int(np.argmax(err))
        if err[at] <= 1.0:
            continue
        mid = a + at
        inside = [c for c in cand if a < c < b]
        if inside:
            mid = min(inside, key=lambda c: abs(c - mid))
        keys.add(mid)
        todo += [(a, mid), (mid, b)]
    return sorted(keys)


def write_joint(values, rest, candidates=(), loop=False):
    """A joint's per-frame values -> (script bytes or None (it stays at its rest), keys): the
    smaller of a cubic script (write_joint_cubic) and a linear one, both within WRITE_TOL."""
    import numpy as np
    cub, ck = write_joint_cubic(values, rest, candidates, loop)
    if cub is None:
        return None, []
    tracks = {JA.NAMES.index(k): [float(v) for v in seq] for k, seq in values.items()}
    tr = sorted(_cmd_tracks(cub))
    tracks = {t: tracks[t] for t in tr}
    V = np.array([tracks[t] for t in tr])
    n = V.shape[1]
    if n < 2:
        return cub, ck
    tol = np.array([WRITE_TOL[_kind(t)] for t in tr])[:, None]
    cand = sorted({int(c) for c in candidates if 0 < c < n - 1})
    keys = _linear_keys(V, tol * 0.6, cand)
    for _ in range(n):                                  # played as written (quantized)
        lin = encode_linear(tracks, keys, loop)
        out, _ = simulate16(lin, 0, n)
        got = np.array([[out[f_].get(t, rest[JA.NAMES[t]]) for f_ in range(n)] for t in tr])
        err = (np.abs(got - V) / tol).max(0)
        at = int(np.argmax(err))
        if err[at] <= 1.0 or at in keys:
            break
        keys = sorted(keys + [at])
    return (lin, keys) if len(lin) < len(cub) else (cub, ck)


def _cmd_tracks(script):
    w = u16(script, 0)
    flags = (w >> 1) & 0x3FF
    return [t for t in range(TRACKS) if (flags >> t) & 1]


def write_joint_cubic(values, rest, candidates=(), loop=False):
    """A joint's per-frame values {track name: [...]} -> (script bytes or None (it stays at
    its rest), keys used). Keys: start with the ends, add one at the worst frame (the
    candidate nearest it when there is one: e.g. the Blender keys) until every frame plays
    within WRITE_TOL, with each key's rate fitted (least squares); then checked by playing
    the written script (quantized), adding keys if that's still out."""
    import numpy as np
    tracks = {}
    for name, seq in values.items():
        t = JA.NAMES.index(name)
        tol = WRITE_TOL[_kind(t)]
        if max(abs(v - rest[name]) for v in seq) > tol / 4 or max(seq) - min(seq) > tol / 4:
            tracks[t] = [float(v) for v in seq]
    if not tracks:
        return None, []
    tr = sorted(tracks)
    V = np.array([tracks[t] for t in tr])
    n = V.shape[1]
    if n < 2:
        keys = [0]
        script = encode_script(tracks, keys, {t: {0: 0.0} for t in tr}, False)
        return script, keys
    tol = np.array([WRITE_TOL[_kind(t)] for t in tr])[:, None]
    cand = sorted({int(c) for c in candidates if 0 < c < n - 1})
    keys = [0, n - 1]
    for _ in range(n):
        rates, played = _fit_rates(V, keys)
        err = (np.abs(played - V) / (tol * 0.6)).max(0)          # (room for the quantization)
        at = int(np.argmax(err))
        if err[at] <= 1.0:
            break
        a = max(k for k in keys if k <= at)
        b = min(k for k in keys if k > at) if at < n - 1 else n - 1
        inside = [c for c in cand if a < c < b]
        mid = min(inside, key=lambda c: abs(c - at)) if inside else at
        if mid in keys:
            mid = (a + b) // 2
        if mid in keys:
            break
        keys = sorted(keys + [mid])
    for _ in range(n):                                  # played as written (quantized)
        rates, _ = _fit_rates(V, keys)
        rmap = {t: {k: float(rates[i][j]) for j, k in enumerate(keys)} for i, t in enumerate(tr)}
        script = encode_script(tracks, keys, rmap, loop)
        out, _ = simulate16(script, 0, n)
        got = np.array([[out[f_].get(t, rest[JA.NAMES[t]]) for f_ in range(n)] for t in tr])
        err = (np.abs(got - V) / tol).max(0)
        at = int(np.argmax(err))
        if err[at] <= 1.0:
            break
        a = max(k for k in keys if k <= at)
        b = min((k for k in keys if k > at), default=n - 1)
        mid = at if at not in keys else (a + b) // 2
        if mid in keys or mid <= a:
            break
        keys = sorted(keys + [mid])
    return script, keys


def write_file(per_joint_scripts):
    """An animation file: [script bytes or None] (the joint order) -> bytes, with its internal
    relocation chain (head: the first non-NULL entry; appender rule)."""
    n = len(per_joint_scripts)
    data = bytearray(4 * n)
    targets = []
    for sc in per_joint_scripts:
        if sc is None:
            targets.append(None)
            continue
        while len(data) % 4:
            data += b'\0'
        targets.append(len(data))
        data += sc
    fields = [4 * i for i, t in enumerate(targets) if t is not None]
    for k, fo in enumerate(fields):
        nxt = fields[k + 1] // 4 if k + 1 < len(fields) else 0xFFFF
        struct.pack_into('>HH', data, fo, nxt, targets[fo // 4] // 4)
    return bytes(data)


def written_flags(flags):
    """The flags a written animation needs: always the 16-bit format (0x8 off)."""
    return flags & ~FLAG_ANIMJOINT


def encode_animation(f, flags, values, frames, loops=None, candidates=None):
    """A whole animation: values {bone name: {track name: per-frame}} (joints left out: their
    rest) for these motion flags -> file bytes, in the 16-bit format (its motion's flags must
    then be written_flags(flags)). loops: {bone: True} for scripts that loop; candidates:
    {bone: key frames to prefer}."""
    order = joint_order(f, flags)
    joints = {f'joint_{j.index:02d}': j for j in f.details['hi']['joints']}
    scripts = []
    for g in order:
        nm = SP.joint_name(g)
        j = joints.get(nm)
        rest = JA.rest_values(j) if j is not None else dict(rx=0.0, ry=0.0, rz=0.0, tx=0.0, ty=0.0, tz=0.0,
                                                            sx=1.0, sy=1.0, sz=1.0)
        v = values.get(nm)
        if v is None:
            scripts.append(None)
            continue
        sc, _ = write_joint(v, rest, (candidates or {}).get(nm, ()), bool((loops or {}).get(nm)))
        scripts.append(sc)
    return write_file(scripts)
