"""Inside Blender: an animation .bin (a fighter animation file, figatree) -> a new action on the
fighter's armature (n64_panel.py: Action Editor > N64 Animation > Load Animation .bin).

The game's joints come from the files the export saved (OUT_DIR/fighter, manifest fighter_files);
the bones from the export's model.gltf (each bone's rest vs the game joint's: the same correction
blend_to_gltf.py uses the other way). Every bone is keyed (what the file doesn't move: its rest),
then the keys are reduced like the exported animations' (keyreduce.TOL_ANIM).

The action is for looking / working with: it isn't one of the exported animations (no
n64_export), so the import doesn't write it back on its own.
"""
import json
import os
import re

import bpy
import numpy as np
from mathutils import Matrix, Quaternion

import model_files as MF
import figatree as FT
import jointanim as JA
import keyreduce as KR

A = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))     # glTF (Y up) -> Blender
Ai = A.inverted()
ROOTS = ('TransN', 'XRotN', 'YRotN')


def export_dir_of(scene):
    d = scene.get('n64_export_dir', scene.get('fighter_export_dir', '//'))
    return os.path.normpath(bpy.path.abspath(d))


def load_fighter(export_dir):
    man = json.load(open(os.path.join(export_dir, 'manifest.json')))
    ff = man.get('fighter_files')
    if not ff:
        raise RuntimeError('this export has no fighter files (export it again with the current tool)')
    return MF.load(os.path.join(export_dir, ff['main']), os.path.join(export_dir, ff['char']),
                   ff['main_int_head'], ff['main_ext_head'], ff['char_int_head'],
                   {int(k): v for k, v in (ff.get('char_ext') or {}).items()})


def load_bin(arm, path, export_dir, flags=None, name=None):
    """-> (action, flags, guessed)."""
    f = load_fighter(export_dir)
    data = open(path, 'rb').read()
    guessed = False
    if flags is None:
        flags, guessed = FT.pick_flags(f, data, FT.GUESS_FLAGS)
    anim = FT.load(f, data, None, flags)
    n = int(anim['frames'])

    # the bones' rest vs the game joints' (as blend_to_gltf.py _GameSpace)
    gl = json.load(open(os.path.join(export_dir, 'model.gltf')))
    nodes = gl['nodes']
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}
    by_name = {nd.get('name'): i for i, nd in enumerate(nodes)}

    def local(nd):
        t = nd.get('translation', [0, 0, 0])
        q = nd.get('rotation', [0, 0, 0, 1])
        sc = nd.get('scale', [1, 1, 1])
        return Matrix.Translation(t) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4() @ \
            Matrix.Diagonal((*sc, 1.0))
    world = {}

    def node_world(i):
        if i not in world:
            world[i] = (node_world(parent[i]) @ local(nodes[i])) if i in parent else local(nodes[i])
        return world[i]
    bones = [b for b in arm.data.bones if b.name in by_name and re.match(r'joint_\d+$|TransN$|XRotN$|YRotN$', b.name)]
    corr = {}
    for b in bones:
        j_rest = (A @ node_world(by_name[b.name]) @ Ai).to_3x3().normalized()
        corr[b.name] = (j_rest.inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
    gparent = {b.name: (nodes[parent[by_name[b.name]]].get('name') if by_name[b.name] in parent else None)
               for b in bones}
    order = []                                     # parents first

    def visit(nm):
        if nm in order or nm not in gparent:
            return
        if gparent[nm]:
            visit(gparent[nm])
        order.append(nm)
    for b in bones:
        visit(b.name)

    joints = {f'joint_{j.index:02d}': j for j in f.details['hi']['joints']}
    rest = {nm: (JA.matrix(JA.rest_values(joints[nm])) if nm in joints else np.eye(4)) for nm in order}
    act = bpy.data.actions.new(name or os.path.splitext(os.path.basename(path))[0])
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = act
    if hasattr(ad, 'action_slot') and ad.action_slot is None and getattr(act, 'slots', None):
        ad.action_slot = act.slots[0]
    pbs = arm.pose.bones
    prev_q = {}
    for fr in range(n):
        G, M = {}, {}
        for nm in order:
            vals = anim['values'].get(nm)
            L = Matrix(JA.matrix({k: vals[k][fr] for k in JA.EDITABLE}).tolist()) if vals else Matrix(rest[nm].tolist())
            G[nm] = (G[gparent[nm]] @ L) if gparent[nm] in G else L
            M[nm] = A @ G[nm] @ Ai @ corr[nm]               # the pose bone's matrix (armature space)
            b = arm.data.bones[nm]
            if b.parent is not None and b.parent.name in M:
                rel = b.parent.matrix_local.inverted() @ b.matrix_local
                basis = rel.inverted() @ M[b.parent.name].inverted() @ M[nm]
            else:
                basis = b.matrix_local.inverted() @ M[nm]
            loc, rot, scale = basis.decompose()
            if nm in prev_q:
                rot.make_compatible(prev_q[nm])
            prev_q[nm] = rot
            pb = pbs[nm]
            pb.rotation_mode = 'QUATERNION'
            pb.location, pb.rotation_quaternion, pb.scale = loc, rot, scale
            for prop in ('location', 'rotation_quaternion', 'scale'):
                pb.keyframe_insert(prop, frame=fr, group=nm)
    hints = {nm: k for nm, k in (anim.get('keys') or {}).items()}
    KR.reduce_fcurves(KR.action_fcurves(act), hints=hints, candidates=True, tol=KR.TOL_ANIM)
    act.use_fake_user = True
    try:
        act.use_frame_range = True
        act.frame_start, act.frame_end = 0, max(1, n - 1)
    except (AttributeError, TypeError):
        pass
    act['n64_flags'] = f'{flags:08X}'
    act['n64_flags_guessed'] = bool(guessed)
    act['n64_loop'] = bool(anim.get('loop'))
    act['n64_source'] = os.path.abspath(path)
    act['n64_actions'] = '(loaded from a file)'
    return act, flags, guessed
