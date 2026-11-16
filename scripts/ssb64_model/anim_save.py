"""Inside Blender: an action on the fighter's armature -> an animation .bin file (figatree)
(n64_panel.py: Action Editor > N64 Animation > Save Animation .bin). The reverse of anim_load.py.

Sampled like the import samples edited animations (blend_gamespace.sample_action: every joint's
local transform in the game's space, each frame) and encoded the same way
(figatree.encode_animation: 16-bit scripts, as few keys as play it within figatree.WRITE_TOL,
Blender's key frames preferred). The file is written in the 16-bit format: the motion that plays
it needs figatree.written_flags(flags) (the flags without 0x8), which save_bin returns.
"""
import blend_gamespace as BGS
import figatree as FT
from anim_load import export_dir_of, load_fighter  # noqa: F401 (export_dir_of: for the panel)


def save_bin(act, path, export_dir, flags, loop=False):
    """-> (flags to play it with, frames, bytes written)."""
    f = load_fighter(export_dir)
    gs = BGS._GameSpace.make(export_dir)
    if gs is None:
        raise RuntimeError('no N64 fighter armature, or the export has no model.gltf')
    restore = gs.keep()
    try:
        frames, vals, keys = BGS.sample_action(gs, act)
    finally:
        restore()
    written = FT.written_flags(flags)
    loops = {b: True for b in vals} if loop else None
    data = FT.encode_animation(f, written, vals, frames, loops=loops, candidates=keys)
    with open(path, 'wb') as fh:
        fh.write(data)
    LAST_WARNINGS[:] = warnings(f, gs, written, vals)
    for w in LAST_WARNINGS:
        print('Save Animation .bin:', w)
    return written, frames, len(data)


LAST_WARNINGS = []      # of the last save_bin (the panel reports them)


def warnings(f, gs, flags, vals):
    """What the saved file can't play as posed in Blender."""
    import jointanim as JA
    import shieldpose as SP
    out = []
    if gs.snapped:
        out.append('scaled on axes that aren\'t the game joint\'s (the bone points elsewhere): stored along '
                   'the nearest game axes: ' + ', '.join(sorted(gs.snapped)))
    in_file = {SP.joint_name(g) for g in FT.joint_order(f, flags)}
    joints = {f'joint_{j.index:02d}': j for j in f.details['hi']['joints']}
    dropped = []
    for b, v in vals.items():
        if b in in_file:
            continue
        rest = JA.rest_values(joints[b]) if b in joints else dict(rx=0.0, ry=0.0, rz=0.0, tx=0.0, ty=0.0,
                                                                     tz=0.0, sx=1.0, sy=1.0, sz=1.0)
        tol = {'r': 1e-3, 't': 0.05, 's': 1e-3}
        if any(abs(x - rest[k]) > tol[k[0]] for k, xs in v.items() if k in rest for x in xs):
            dropped.append(b)
    if dropped:
        out.append(f'not in this animation (flags 0x{flags:08X} leave them out of its joints): '
                   + ', '.join(sorted(dropped)) + ' - their animation is dropped')
    return out
