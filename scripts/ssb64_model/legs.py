"""The fighter's legs for the game's slope foot IK (decomp mpcommon.c
mpCommonUpdateFighterSlopeContour, ftparam.c func_ovl2_800EBC0C / 800EBD08): FTAttributes

  + 0x2FC joint_rfoot_id      s32  fp->joints index of the right thigh joint
  + 0x300 joint_rfoot_rotate  f32  (a length, not an angle) the right shin's
  + 0x304 joint_lfoot_id      s32  the left thigh joint
  + 0x308 joint_lfoot_rotate  f32  the left shin's

On a slope (motion event SetSlopeContour: feet), each leg is bent by 2-bone IK so the point
`rotate` units down the shin (the knee's x axis) touches the floor: the thigh joint (its child, at
the same place, is the hip) -> the knee (that child's child, `thigh length` along x) -> the shin
point. Every vanilla fighter: the thigh joint has one child at no offset, the knee sits along
its x axis, `rotate` is ~0.925 x the knee -> ankle distance (Yoshi: 1.0); right leg after left.

The IK doesn't put that point ON the floor: it moves it up or down by the floor's height
difference under it (func_ovl2_800DDF74), so any point on the shin works and a base
character's length is fine. What matters: the ids point at the legs (else the IK bends other
joints), and the animations keep the hip at the thigh joint and the knee at its rest offset
along x (the IK assumes both; it reads the knee's current translate.x as the thigh length).
fix() (lengths to SHIN_RATIO x the shin) is kept for reference; the tools only check() the ids."""
import struct

import numpy as np

import model_files as MF
import shieldpose as SP

A_RFOOT_ID, A_RFOOT_LEN, A_LFOOT_ID, A_LFOOT_LEN = 0x2FC, 0x300, 0x304, 0x308
SHIN_RATIO = 0.925               # vanilla: rotate / knee -> ankle distance
OK_RATIO = (0.85, 1.05)          # a value in this range of the shin is left as it is


def read(f, main=None):
    d = f.main if main is None else main
    return dict(r_id=struct.unpack_from('>i', d, f.attr + A_RFOOT_ID)[0], r_len=MF.f32(d, f.attr + A_RFOOT_LEN),
                l_id=struct.unpack_from('>i', d, f.attr + A_LFOOT_ID)[0], l_len=MF.f32(d, f.attr + A_LFOOT_LEN))


def _along_x(t, tol=0.1):
    """A translation mostly along +x (a bone pointing down its x axis)."""
    n = float(np.linalg.norm(t))
    return n > 1e-3 and t[0] > 0 and np.hypot(t[1], t[2]) <= tol * n


def chains(f):
    """The leg-like chains in the fighter's tree (setup_parts): [(thigh id, knee id, ankle id,
    thigh length, shin length, ankle height, hip x, hip height (at rest))] in joint order. Thigh: one child at no offset (the hip);
    knee: that one's child, along x; ankle: the knee's child, along x; at rest, each lower
    than the one before. (Arms often match too: expected() picks the legs.)"""
    J = f.details['hi']['joints']
    kids = SP.guard_tree(f, 0)
    body = MF.JOINT_COMMON_START

    def t(g):
        return np.asarray(J[g - body].t, float)
    out = []
    for g in sorted(kids):
        if g < body or len(kids[g]) != 1:
            continue
        hip = kids[g][0]
        if np.linalg.norm(t(hip)) > 1e-2:
            continue
        knee = next((c for c in kids.get(hip, []) if _along_x(t(c))), None)
        if knee is None:
            continue
        ankle = next((c for c in kids.get(knee, []) if _along_x(t(c), 0.4)), None)
        if ankle is None:
            continue
        y = [float(np.asarray(J[x - body].world)[1, 3]) for x in (g, knee, ankle)]
        thigh, shin = float(np.linalg.norm(t(knee))), float(np.linalg.norm(t(ankle)))
        if not (y[0] - y[1] > 0.5 * thigh and y[1] - y[2] > 0.5 * shin):
            continue                                    # (not pointing down: an arm)
        x = float(np.asarray(J[g - body].world)[0, 3])
        out.append((g, knee, ankle, thigh, shin, y[2], x, y[0]))
    return out


def expected(f):
    """What the foot attributes should be for this skeleton: dict(r_id, r_len, l_id, l_len), or
    None when there aren't two legs (left the first in joint order, as in vanilla)."""
    c = chains(f)
    # the legs: a mirrored pair (hips on opposite sides at one height, similar lengths), the
    # lowest ankles of those
    best = None
    for i, a in enumerate(c):
        for b in c[i + 1:]:
            span = max(a[3] + a[4], b[3] + b[4])
            if a[6] * b[6] >= 0 or abs(a[7] - b[7]) > 0.15 * span or abs(a[3] - b[3]) > 0.15 * max(a[3], b[3]):
                continue
            k = a[5] + b[5]
            if best is None or k < best[0]:
                best = (k, a, b)
    if best is None:
        return None
    (l, _, _, _, l_shin, *_), (r, _, _, _, r_shin, *_) = best[1], best[2]
    return dict(r_id=r, r_len=round(r_shin * SHIN_RATIO, 3), l_id=l, l_len=round(l_shin * SHIN_RATIO, 3),
                r_shin=r_shin, l_shin=l_shin)


def check(f, main=None):
    """-> (current, expected, [what's off], note). The ids are kept as they are when they point
    at leg chains: only their lengths are checked (vs SHIN_RATIO x the shin). Ids that don't:
    nothing is changed, the note says which joints look like the legs."""
    cur = read(f, main)
    by_id = {c[0]: c for c in chains(f)}
    if cur['r_id'] in by_id and cur['l_id'] in by_id:
        r_shin, l_shin = by_id[cur['r_id']][4], by_id[cur['l_id']][4]
        exp = dict(r_id=cur['r_id'], r_len=round(r_shin * SHIN_RATIO, 3), l_id=cur['l_id'],
                   l_len=round(l_shin * SHIN_RATIO, 3), r_shin=r_shin, l_shin=l_shin)
        off = [f'{s_}_len' for s_ in ('r', 'l')
               if not OK_RATIO[0] * exp[f'{s_}_shin'] <= cur[f'{s_}_len'] <= OK_RATIO[1] * exp[f'{s_}_shin']]
        return cur, exp, off, None
    guess = expected(f)
    note = (f"slope foot IK: joints {cur['r_id']} / {cur['l_id']} (attributes + 0x2FC / 0x304) don't look like "
            "legs (thigh -> hip at the same place -> knee -> ankle)" +
            (f"; the legs look like joints {guess['r_id']} (right) / {guess['l_id']} (left): check, and set them "
             "by hand if so" if guess else '') + ". Not changed.")
    return cur, None, [], note


def fix(f, main):
    """Write the expected shin lengths into main (bytearray) where they're off -> message or None."""
    cur, exp, off, note = check(f, main)
    if note:
        return note
    if not off:
        return None
    struct.pack_into('>f', main, f.attr + A_RFOOT_LEN, exp['r_len'])
    struct.pack_into('>f', main, f.attr + A_LFOOT_LEN, exp['l_len'])
    return (f"slope foot IK: shin lengths (attributes + 0x300 / 0x308) {cur['r_len']:g} / {cur['l_len']:g} -> "
            f"{exp['r_len']:g} / {exp['l_len']:g} (joints {exp['r_id']} / {exp['l_id']}: "
            f"{SHIN_RATIO} x their knee -> ankle, as vanilla)")
