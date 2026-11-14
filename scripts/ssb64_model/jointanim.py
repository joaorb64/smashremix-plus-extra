"""Joint (DObj) animations: a stage layer's anim_joints scripts (decomp objanim.c
gcParseDObjAnimJoint / gcPlayDObjAnimJoint) <-> per-frame joint transforms.

A joint's script animates its local transform's tracks (flag bits): 0..2 rotation x / y / z
(radians; the joint's matrix is T * Rz Ry Rx * S), 3 the translation path (TraI: kept, not
editable), 4..6 translation, 7..9 scale. The commands are the same as the material scripts'
(matanim.py), so a script is written with matanim.encode and checked by playing it with
model_files.simulate_anim.

play(): a joint's original script -> its values per frame, cycle / end. decompose(): a local
matrix -> those values (the inverse of model_files.rot_zyx). build(): values per frame (from
Blender) -> a script, as keyframes where they're exact, frame by frame otherwise.
"""
import math

import numpy as np

import model_files as MF
import matanim as MA

NAMES = ('rx', 'ry', 'rz', 'ti', 'tx', 'ty', 'tz', 'sx', 'sy', 'sz')
EDITABLE = ('rx', 'ry', 'rz', 'tx', 'ty', 'tz', 'sx', 'sy', 'sz')
ROT = ('rx', 'ry', 'rz')
TOL = {'rx': 2e-4, 'ry': 2e-4, 'rz': 2e-4}             # radians


def tol(k, v):
    """How far a played value may be from Blender's: angles 2e-4 rad; translations 0.05 units
    (+ float32 rounding of large values); scales 1e-4."""
    if k in TOL:
        return TOL[k]
    if k in ('tx', 'ty', 'tz'):
        return 0.05 + 2e-6 * abs(v)
    return 1e-4 * max(1.0, abs(v))


def rest_values(joint):
    return dict(rx=float(joint.r[0]), ry=float(joint.r[1]), rz=float(joint.r[2]),
                tx=float(joint.t[0]), ty=float(joint.t[1]), tz=float(joint.t[2]),
                sx=float(joint.s[0]), sy=float(joint.s[1]), sz=float(joint.s[2]))


def uses_interp(d, ptrs, script, limit=4096):
    """Does the script set a translation path (TraI, command 13)? Those aren't editable."""
    o, seen = script, set()
    for _ in range(limit):
        if o is None or o + 4 > len(d) or o in seen:
            return False
        seen.add(o)
        w = MF.u32(d, o)
        op, flags = w >> 25, (w >> 15) & 0x3FF
        if op == 13:
            return True
        if op in (0,):
            return False
        if op in (1, 14):
            o = ptrs.get(o + 4)
            continue
        n = bin(flags).count('1') if op in (3, 4, 7, 8, 9, 10, 11, 17) else 0
        n = 2 * bin(flags).count('1') if op in (5, 6) else n
        o += 4 + 4 * n
    return False


def play(d, ptrs, script, joint, max_frames=1800, max_loop=8192):
    """A joint's script -> dict(frames, loop, loop_start, samples={track: per frame 0..frames},
    tracks=[the tracks it sets]) like matanim.play: a cycle ends with its first frame again."""
    keys = {}
    out, loop = MF.simulate_anim(d, ptrs, script, max_loop + 1, keys)
    rest = rest_values(joint)
    looping, n = False, None
    if loop == 0:
        n = 0
    elif loop:
        more, _ = MF.simulate_anim(d, ptrs, script, 2 * loop + 1)
        if len(more) > 2 * loop and all(more[k] == more[loop + k] for k in range(loop + 1)):
            out, n, looping = more, loop, True
        else:
            n = loop
    if n is None:
        n = min(max_frames, len(out) - 1)
    tracks = sorted({k for fr in out[:n + 1] for k in fr if k != 3})
    samples = {}
    for name in EDITABLE:
        i = NAMES.index(name)
        cur, seq = rest[name], []
        for fr in out[:n + 1]:
            cur = fr.get(i, cur)
            seq.append(float(cur))
        samples[name] = seq
    keys = group_keys(keys, n)
    return dict(frames=n, loop=looping, loop_start=0, samples=samples, tracks=[NAMES[i] for i in tracks],
                keys=keys)


GROUPS = {'rotation': (0, 1, 2), 'location': (4, 5, 6), 'scale': (7, 8, 9)}


def group_keys(track_keys, last, starts=()):
    """Per-track key frames ({track: frames}, simulate_anim's) -> per Blender channel group
    {'rotation' | 'location' | 'scale': sorted frames}: the union of its tracks' keys (a bone's
    rotation curves turn all three axes at once), with the ends (and `starts`) when the group
    is animated at all; [] when the script never sets it (constant: one key)."""
    out = {}
    for g, tracks in GROUPS.items():
        ks = set()
        for t in tracks:
            ks |= {k for k in track_keys.get(t, ()) if 0 <= k <= last}
        out[g] = sorted(ks | {0, last} | set(starts)) if ks else []
    return out


def decompose(L, prev=None):
    """A joint's local 4x4 matrix (T * Rz Ry Rx * S) -> {track: value}. prev: the previous
    frame's values: of the two rotation solutions and the 2 pi turns, the closest one."""
    t = L[:3, 3]
    M = L[:3, :3]
    s = np.linalg.norm(M, axis=0)
    zero = s < 1e-9
    if zero.all():                                     # scaled to nothing (hidden): no rotation to read
        r = prev or {}
        return dict(rx=r.get('rx', 0.0), ry=r.get('ry', 0.0), rz=r.get('rz', 0.0),
                    tx=float(t[0]), ty=float(t[1]), tz=float(t[2]), sx=0.0, sy=0.0, sz=0.0)
    if zero.any():                                     # flattened: the zero axes' directions are
        cols = [M[:, i] / s[i] if not zero[i] else None for i in range(3)]   # any that complete it
        if zero.sum() == 2:
            i = int(np.flatnonzero(~zero)[0])
            a = cols[i]
            helper = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
            b = np.cross(a, helper)
            cols[(i + 1) % 3] = b / np.linalg.norm(b)
        k = int(np.flatnonzero([c is None for c in cols])[0])
        cols[k] = np.cross(cols[(k + 1) % 3], cols[(k + 2) % 3])
        R = np.stack(cols, axis=1)
    else:
        if np.linalg.det(M) < 0:                       # mirrored: negative scales
            s = -s
        R = M / s
    p = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    cands = []
    if abs(math.cos(p)) > 1e-9:
        r = math.atan2(R[2, 1], R[2, 2])
        h = math.atan2(R[1, 0], R[0, 0])
        cands += [(r, p, h), (r + math.pi, math.pi - p, h + math.pi)]
    if abs(math.cos(p)) < 1e-2:                         # near gimbal (y = +-90 degrees: x and z turn
        h = 0.0 if prev is None else prev['rz']         # about one axis): keep z, the rest is x
        for pp in (p, math.copysign(math.pi / 2, p)):
            cy, sy, cz, sz = math.cos(pp), math.sin(pp), math.cos(h), math.sin(h)
            Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            Rx = Ry.T @ Rz.T @ R
            cands.append((math.atan2(Rx[2, 1], Rx[1, 1]), pp, h))

    def near(a, ref):
        return a + 2 * math.pi * round((ref - a) / (2 * math.pi))
    # the solutions that give the matrix back (near gimbal the regular one is numerically lost),
    # the closest to the previous frame's
    errs = [float(np.abs(MF.rot_zyx(*c) - R).max()) for c in cands]
    good = [c for c, e in zip(cands, errs) if e <= min(errs) + 1e-6]
    if prev is not None:
        best = None
        for c in good:
            c = tuple(near(a, prev[k]) for a, k in zip(c, ROT))
            dist = sum((a - prev[k]) ** 2 for a, k in zip(c, ROT))
            if best is None or dist < best[0]:
                best = (dist, c)
        r, p, h = best[1]
    else:
        r, p, h = good[0]
    return dict(rx=r, ry=p, rz=h, tx=float(t[0]), ty=float(t[1]), tz=float(t[2]),
                sx=float(s[0]), sy=float(s[1]), sz=float(s[2]))


def matrix(v):
    """{track: value} -> the joint's local 4x4 matrix (T * Rz Ry Rx * S)."""
    M = np.eye(4)
    M[:3, :3] = MF.rot_zyx(v['rx'], v['ry'], v['rz']) * [v['sx'], v['sy'], v['sz']]
    M[:3, 3] = [v['tx'], v['ty'], v['tz']]
    return M


def same_transform(a, b):
    """Do two value dicts give the same local matrix? (Compared as matrices: angles a turn
    apart, a mirror written as rotation + negative scale, a hidden joint's rotation...
    are the same transform.)"""
    A, B = matrix(a), matrix(b)
    big = max(1.0, float(np.abs(B[:3, :3]).max()))
    if np.abs(A[:3, :3] - B[:3, :3]).max() > 2e-4 * big:
        return False
    return all(abs(A[i, 3] - B[i, 3]) <= tol('tx', B[i, 3]) for i in range(3))


def unchanged(orig, new):
    """Blender's animation (new: frames, loop, loop_start, samples) = the original script's?"""
    if (orig['frames'], orig['loop'], orig['loop_start']) != (new['frames'], new['loop'], new.get('loop_start', 0)):
        return False
    for n in range(orig['frames'] + 1):
        a = {k: orig['samples'][k][n] for k in EDITABLE}
        b = {k: new['samples'][k][n] for k in EDITABLE}
        if not same_transform(b, a):
            return False
    return True


def _check(script, ptrs, samples, tracks, frames, loop, loop_start):
    d = script + b'\0' * 8
    period = frames - loop_start
    total = frames + 1 + (period if loop else 0)
    out, _ = MF.simulate_anim(d, dict(ptrs), 0, total)
    bad = []
    for k in tracks:
        i, seq = NAMES.index(k), samples[k]
        for n in range(len(out)):
            want = seq[loop_start + (n - frames) % period] if loop and period > 0 and n >= frames else \
                seq[min(n, len(seq) - 1)]
            have = out[n].get(i)
            if have is None or abs(have - want) > tol(k, want):
                bad.append(k)
                break
    return bad


def build(anim, rest, keep_tracks=()):
    """A joint's animation from Blender (frames, loop, loop_start, samples {track: values
    0..frames}) -> (script bytes, pointer fields, [tracks written frame by frame]). Tracks
    that stay at the joint's rest value aren't written (unless in keep_tracks: the original
    animated them)."""
    frames, loop, loop_start = int(anim['frames']), bool(anim['loop']), int(anim.get('loop_start', 0))
    samples = {k: [float(v) for v in anim['samples'][k][:frames + 1]] for k in EDITABLE}
    tracks = [k for k in EDITABLE if k in keep_tracks or any(abs(v - rest[k]) > tol(k, rest[k]) for v in samples[k])]
    if not tracks:
        return None, [], []
    if any(not math.isfinite(v) or abs(v) > 3e38 for k in tracks for v in samples[k]):
        raise ValueError('a value past what the game stores (float32)')
    keys = {}
    for k in tracks:
        seq = [MA.f32r(v) for v in samples[k]]          # (what float32 keeps)
        keys[k] = [(t, v, ip, 0.0, 0.0) for t, v, ip in MA.to_keys(seq, k, tol=lambda v, k=k: tol(k, v) / 2)]
    script, ptrs = MA.encode(keys, frames, loop, loop_start, names=NAMES)
    bad = _check(script, ptrs, samples, tracks, frames, loop, loop_start)
    if bad:
        for k in bad:
            keys[k] = [(n, v, 'LINEAR', 0.0, 0.0) for n, v in enumerate(samples[k])]
        script, ptrs = MA.encode(keys, frames, loop, loop_start, names=NAMES)
    return script, ptrs, bad
