"""Joint animations with keys only where they're needed.

The game's joint scripts (objanim.c) are keyframes too: a value and a rate at a frame, and a
cubic (or linear, or a step) to the next one. The glTF carries them sampled once per frame
(export_model.py Exporter.animation: what the game plays), so Blender's importer makes a key
on every frame. This rebuilds each bone's curves with keys only where they're needed: the
game's keyframe frames when known (hints), more where a curve can't follow the samples
within tolerance, with Bezier handles fitted to the samples between keys (a Bezier whose
handles sit at a third of the segment is the game's cubic Hermite, exactly). Every frame
still plays within tolerance of the samples, so the import (blend_to_gltf.py samples the
pose per frame) gets the same values back.

fit() is plain numpy; reduce_fcurves() applies it to Blender F-curves (inside Blender).
"""
import re

import numpy as np

# how far a reduced curve may be from the game's value at any frame, per channel: quaternion
# components 2e-5 (~4e-5 rad: under jointanim.TOL, so an unedited round trip keeps its scripts),
# locations 0.005 units, scales 1e-5; anything else 1e-4
TOL = {'rotation_quaternion': 2e-5, 'rotation_euler': 4e-5, 'location': 5e-3, 'scale': 1e-5}
TOL_DEFAULT = 1e-4


def _segment(V, a, b):
    """Hermite tangents (per segment, dv/du) for samples V[:, a..b] with the end values fixed:
    least squares over the frames between (the error then is as small as a cubic gets)."""
    n = b - a
    p0, p1 = V[:, a], V[:, b]
    if n < 2:
        d = p1 - p0
        return d, d
    u = np.arange(1, n) / n
    u2, u3 = u * u, u * u * u
    h00, h01 = 2 * u3 - 3 * u2 + 1, -2 * u3 + 3 * u2
    h10, h11 = u3 - 2 * u2 + u, u3 - u2
    A = np.stack([h10, h11], 1)                                 # (n-1, 2)
    R = V[:, a + 1:b] - (np.outer(p0, h00) + np.outer(p1, h01))  # (channels, n-1)
    sol, *_ = np.linalg.lstsq(A, R.T, rcond=None)               # (2, channels)
    return sol[0], sol[1]


def _eval(V, a, b, m0, m1):
    n = b - a
    u = np.arange(0, n + 1) / n
    u2, u3 = u * u, u * u * u
    return (np.outer(V[:, a], 2 * u3 - 3 * u2 + 1) + np.outer(m0, u3 - 2 * u2 + u) +
            np.outer(V[:, b], -2 * u3 + 3 * u2) + np.outer(m1, u3 - u2))


def fit(V, tol, hints=(), exact=False, candidates=False):
    """V: (channels, frames) samples, one per frame; tol: per channel. -> [(a, b, m0, m1)]
    segments (key frames a < b, Hermite tangents per channel) that play every frame within
    tol: keys at the ends and the hints, then split where the error is worst. exact: keys at
    the ends and the hints only (the game's keyframes), however close the curve gets between.
    candidates: the hints are where keys may go, not keys: start from the ends, split each
    segment that's out of tolerance at the hint nearest its worst frame (at that frame when no
    hint is inside it): the fewest of the game's keys that follow it."""
    V = np.asarray(V, float)
    tol = np.asarray(tol, float)[:, None]
    last = V.shape[1] - 1
    if last <= 0:
        return []
    cand = sorted({int(h) for h in hints if 0 < h < last})
    keys = [0, last] if candidates else sorted({0, last} | set(cand))
    todo = list(zip(keys, keys[1:]))
    done = []
    while todo:
        a, b = todo.pop()
        m0, m1 = _segment(V, a, b)
        err = np.abs(_eval(V, a, b, m0, m1) - V[:, a:b + 1]) / tol
        worst = int(np.argmax(err.max(0)))
        if not exact and b - a > 1 and err[:, worst].max() > 1.0:
            mid = a + worst
            if candidates:
                inside = [h for h in cand if a < h < b]
                if inside:
                    mid = min(inside, key=lambda h: abs(h - mid))
            if mid in (a, b):
                mid = (a + b) // 2
            todo += [(a, mid), (mid, b)]
        else:
            done.append((a, b, m0, m1))
    return sorted(done, key=lambda s: s[0])


def _bone_of(fc):
    m = re.match(r'pose\.bones\["(.+)"\]\.(\w+)$', fc.data_path)
    return (m.group(1), m.group(2)) if m else (None, None)


def action_fcurves(act, slot=None):
    """An action's F-curves (Blender 4.4+: its layers' channelbags), [] if none."""
    if act is None:
        return []
    if hasattr(act, 'layers') and act.layers:
        out = []
        for layer in act.layers:
            for strip in layer.strips:
                for cb in getattr(strip, 'channelbags', []):
                    if slot is None or cb.slot == slot:
                        out += list(cb.fcurves)
        return out
    return list(getattr(act, 'fcurves', []) or [])


def _samples(fc):
    """The curve's value on every whole frame from its first key to its last (keys come
    back from the glTF import at 499.99997...; between keys: evaluated)."""
    pts = fc.keyframe_points
    if not len(pts):
        return None, None
    f0 = int(round(pts[0].co.x))
    f1 = int(round(pts[-1].co.x))
    at = {int(round(kp.co.x)): kp.co.y for kp in pts}
    return f0, np.array([at[f] if f in at else fc.evaluate(f) for f in range(f0, f1 + 1)])


GROUP_OF = {'rotation_quaternion': 'rotation', 'rotation_euler': 'rotation', 'rotation_axis_angle': 'rotation',
            'location': 'location', 'scale': 'scale'}


# fighter animations reduced (candidates): how far from the game's motion a frame may play
TOL_ANIM = {'rotation_quaternion': 0.004, 'rotation_euler': 0.008, 'location': 0.25, 'scale': 0.002}


def reduce_fcurves(fcurves, hints=None, exact=False, candidates=False, tol=None):
    """Rebuild bone F-curves (pose.bones["X"].location / rotation_* / scale) with keys only
    where needed. A bone's rotation curves share their key frames (a quaternion turns all
    three axes at once: the union of the game's rotation x / y / z keys), and so do its
    location and its scale curves; a group that never changes keeps one key.
    hints: the game's key frames: {bone: {'rotation' | 'location' | 'scale': frames}} ([]:
    the game never sets it), {bone: frames} (every group), or frames (every bone).
    exact: keys on those frames only (and the ends): the key values are the game's, the curves
    between them the closest fit (Blender turns bones from their rest, the game interpolates
    Euler angles: not always the same curve). Else more keys where a curve can't follow the
    samples within tolerance. candidates: as few keys as follow the samples within `tol`
    (default TOL), taken from the hints where they can be (see fit). Modifiers (Cycles) are kept.
    -> (keys before, keys after, worst frame's distance from the samples: dict(rotation in
    degrees, location in units, scale, ratio = worst / tolerance: <= 1 is within))."""
    groups, reference = {}, {}
    for fc in fcurves:
        bone, prop = _bone_of(fc)
        if bone is not None and prop in GROUP_OF and len(fc.keyframe_points) > 2:
            groups.setdefault((bone, GROUP_OF[prop]), []).append((prop, fc))
            reference[(fc.data_path, fc.array_index)] = _samples(fc)
    before = after = 0
    for (bone, grp), lst in groups.items():
        rows, tols, fcs, start = [], [], [], None
        for prop, fc in sorted(lst, key=lambda x: (x[0], x[1].array_index)):
            f0, v = _samples(fc)
            if start is None:
                start, n = f0, len(v)
            if f0 != start or len(v) != n:             # (a curve keyed on its own range)
                continue
            rows.append(v)
            tols.append((tol or TOL).get(prop, TOL_DEFAULT))
            fcs.append((prop, fc))
        if not rows:
            continue
        V = np.array(rows)
        if grp == 'rotation' and len(rows) == 4:   # one hemisphere throughout (q, -q: same turn)
            for k in range(1, V.shape[1]):
                if np.dot(V[:, k], V[:, k - 1]) < 0:
                    V[:, k] = -V[:, k]
        h = hints.get(bone, ()) if isinstance(hints, dict) else (hints or ())
        if isinstance(h, dict):
            h = h.get(grp)
        tol_col = np.array(tols)[:, None]
        constant = bool((np.abs(V - V[:, :1]) <= tol_col).all())
        if constant:
            keys, out_m, in_m = [0], {}, {}         # never changes: one key
        else:
            segs = fit(V, tols, [x - start for x in (h or ())], exact=exact, candidates=candidates)
            if not segs:
                continue
            keys = [segs[0][0]] + [s[1] for s in segs]
            out_m = {s[0]: s for s in segs}             # tangents leaving a key
            in_m = {s[1]: s for s in segs}              # tangents arriving at a key
        for c, (prop, fc) in enumerate(fcs):
            before += len(fc.keyframe_points)
            pts = fc.keyframe_points
            pts.clear()
            pts.add(len(keys))
            for kp, k in zip(pts, keys):
                x, y = float(start + k), float(V[c, k])
                kp.co = (x, y)
                kp.interpolation = 'BEZIER'
                kp.handle_left_type = kp.handle_right_type = 'FREE'
                # a Bezier with handles at thirds of the segment is the Hermite cubic
                so = out_m.get(k)
                si = in_m.get(k)
                if so is not None:
                    L = so[1] - so[0]
                    kp.handle_right = (x + L / 3.0, y + so[2][c] / 3.0)
                if si is not None:
                    L = si[1] - si[0]
                    kp.handle_left = (x - L / 3.0, y - si[3][c] / 3.0)
                if so is None and si is None:          # a single key
                    kp.handle_left, kp.handle_right = (x - 1.0, y), (x + 1.0, y)
                elif so is None:                       # the last key: mirror the left handle
                    kp.handle_right = (2 * x - kp.handle_left[0], 2 * y - kp.handle_left[1])
                elif si is None:                       # the first key: mirror the right one
                    kp.handle_left = (2 * x - kp.handle_right[0], 2 * y - kp.handle_right[1])
            fc.update()
            after += len(keys)
    return before, after, max_error([fc for lst in groups.values() for _, fc in lst], reference)


def max_error(fcurves, reference):
    """How far the curves play from the samples taken before reducing (reference: {(data_path,
    index): (f0, values)}), at their worst frame: dict(rotation (degrees: quaternions as the
    angle between the turns, Euler per axis), location (units), scale, ratio (worst / its
    tolerance: <= 1 is within))."""
    out = dict(rotation=0.0, location=0.0, scale=0.0, ratio=0.0)
    quats = {}
    for fc in fcurves:
        ref = reference.get((fc.data_path, fc.array_index))
        if ref is None:
            continue
        f0, v = ref
        bone, prop = _bone_of(fc)
        got = np.array([fc.evaluate(f0 + i) for i in range(len(v))])
        if prop == 'rotation_quaternion':
            quats.setdefault(bone, {})[fc.array_index] = (got, v)
            continue
        d = float(np.abs(got - v).max())
        out['ratio'] = max(out['ratio'], d / TOL.get(prop, TOL_DEFAULT))
        if prop == 'rotation_euler':
            out['rotation'] = max(out['rotation'], np.degrees(d))
        elif prop in ('location', 'scale'):
            out[prop] = max(out[prop], d)
    for parts in quats.values():
        if len(parts) != 4:
            continue
        G = np.array([parts[i][0] for i in range(4)])
        R = np.array([parts[i][1] for i in range(4)])
        G = G / np.linalg.norm(G, axis=0)
        R = R / np.linalg.norm(R, axis=0)
        dot = np.clip(np.abs((G * R).sum(0)), 0.0, 1.0)
        ang = float(2 * np.arccos(dot).max())
        out['rotation'] = max(out['rotation'], np.degrees(ang))
        out['ratio'] = max(out['ratio'], ang / (2 * TOL['rotation_quaternion']))
    return out


def rebase_actions(arm, old_rest, actions):
    """Bones whose rest turned after the glTF import (build_blend.py tidy_bones aims them at their
    children): the importer wrote each action's keys in the bones' axes as they were then. Keeps
    every pose: a bone whose rest axes turned by C (old @ C = new) gets basis' = C^-1 basis C,
    i.e. location' = C^-1 location, quaternion' = c^-1 q c, scale' = the diagonal of C^-1 S C
    (exact for uniform scales and quarter turns). old_rest: {bone: 3x3 rest (armature space) before}. -> curves changed."""
    import mathutils
    conj = {}
    for b in arm.data.bones:
        old = old_rest.get(b.name)
        if old is None:
            continue
        C = (old.inverted() @ b.matrix_local.to_3x3()).normalized()
        if max(abs(C[i][j] - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3)) < 1e-6:
            continue
        conj[b.name] = C
    changed = 0
    for act in actions:
        fcs = action_fcurves(act)
        by = {}
        for fc in fcs:
            bone, prop = _bone_of(fc)
            if bone in conj:
                by.setdefault((bone, prop), {})[fc.array_index] = fc
        for (bone, prop), comps in by.items():
            C = conj[bone]
            Ci = C.inverted()
            n = len(comps)
            if prop == 'location' and n == 3:
                M = [[Ci[i][j] for j in range(3)] for i in range(3)]
            elif prop == 'scale' and n == 3:
                # the diagonal of C^-1 S C (C a rotation): sum_j Ci[i][j]^2 s_j (1, 1, 1 stays)
                M = [[Ci[i][j] ** 2 for j in range(3)] for i in range(3)]
            elif prop == 'rotation_quaternion' and n == 4:
                c = C.to_quaternion()
                ci = c.inverted()
                # q' = ci * q * c: linear in q; its matrix from the basis quaternions
                cols = []
                for k in range(4):
                    e = mathutils.Quaternion([1.0 if t == k else 0.0 for t in range(4)])
                    r = ci @ e @ c
                    cols.append([r[0], r[1], r[2], r[3]])
                M = [[cols[j][i] for j in range(4)] for i in range(4)]
            else:
                continue
            curves = [comps[i] for i in range(n)]
            npts = len(curves[0].keyframe_points)
            if any(len(fc.keyframe_points) != npts for fc in curves):
                continue                  # (components keyed apart: left as they are)
            for k in range(npts):
                v = [fc.keyframe_points[k].co.y for fc in curves]
                for i, fc in enumerate(curves):
                    kp = fc.keyframe_points[k]
                    kp.co.y = sum(M[i][j] * v[j] for j in range(n))
            for fc in curves:
                for kp in fc.keyframe_points:
                    kp.handle_left_type = kp.handle_right_type = 'AUTO_CLAMPED'
                fc.update()
                changed += 1
    return changed


def action_signature(act, bone=None):
    """A fingerprint of an action's keys (values, frames, handles, interpolation): build_blend
    stores it, blend_to_gltf compares it to tell the actions edited since. bone: that bone's
    F-curves only."""
    import hashlib
    h = hashlib.sha1()
    fcs = [fc for fc in action_fcurves(act) if bone is None or _bone_of(fc)[0] == bone]
    for fc in sorted(fcs, key=lambda c: (c.data_path, c.array_index)):
        h.update(f'{fc.data_path}[{fc.array_index}]'.encode())
        for kp in fc.keyframe_points:
            h.update(('%.4f %.6f %.4f %.6f %.4f %.6f %s;' % (
                kp.co.x, kp.co.y, kp.handle_left.x, kp.handle_left.y, kp.handle_right.x, kp.handle_right.y,
                kp.interpolation)).encode())
    return h.hexdigest()


def bone_signatures(act):
    """{bone: action_signature of its F-curves} (the shield pose: which bones were edited)."""
    bones = {_bone_of(fc)[0] for fc in action_fcurves(act)} - {None}
    return {b: action_signature(act, b) for b in sorted(bones)}
