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
    return written, frames, len(data)
