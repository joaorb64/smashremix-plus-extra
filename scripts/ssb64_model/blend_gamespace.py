"""Inside Blender: the fighter armature's bones in the game's space (the joints' local matrices,
glTF Y up), and an action sampled into the game's per-frame joint tracks. Used by
blend_to_gltf.py (the import: edited animations) and anim_save.py (Save Animation .bin)."""
import json
import os
import re

import bpy


class _GameSpace:
    """The fighter armature's bones in the game's space: sample(frame) -> {bone: its local 4x4
    matrix (numpy) as the game's joint (glTF Y up, the joint's own axes, relative to its parent
    joint)}. None when there's no fighter armature or no export model.gltf."""

    @classmethod
    def make(cls, export_dir):
        arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and 'n64_hidden_parts' in o.keys()), None)
        gl_path = os.path.join(export_dir, 'model.gltf')
        if arm is None or not os.path.exists(gl_path):
            return None
        return cls(arm, json.load(open(gl_path)))

    def __init__(self, arm, gl):
        from mathutils import Matrix, Quaternion
        self.arm = arm
        nodes = self.nodes = gl['nodes']
        self.parent = {c: i for i, n in enumerate(nodes) for c in n.get('children', [])}
        self.by_name = {n.get('name'): i for i, n in enumerate(nodes)}

        def local(n):
            t = n.get('translation', [0, 0, 0])
            q = n.get('rotation', [0, 0, 0, 1])
            sc = n.get('scale', [1, 1, 1])
            return Matrix.Translation(t) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4() @ \
                Matrix.Diagonal((*sc, 1.0))
        world = {}

        def node_world(i):
            if i not in world:
                world[i] = (node_world(self.parent[i]) @ local(nodes[i])) if i in self.parent else local(nodes[i])
            return world[i]
        self.A = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))     # glTF (Y up) -> Blender
        self.Ai = self.A.inverted()
        self.corr = {}
        for b in arm.data.bones:
            if b.name in self.by_name:
                j_rest = (self.A @ node_world(self.by_name[b.name]) @ self.Ai).to_3x3().normalized()
                self.corr[b.name] = (j_rest.inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
        self.bones = [b for b in self.corr if re.match(r'joint_\d+$|TransN$|XRotN$|YRotN$', b)]
        self.ad = arm.animation_data or arm.animation_data_create()

    def use(self, act):
        ad = self.ad
        ad.action = act
        if hasattr(ad, 'action_slot') and ad.action_slot is None and getattr(act, 'slots', None):
            ad.action_slot = act.slots[0]

    def sample(self, fr):
        import numpy as np
        bpy.context.scene.frame_set(fr)
        corr, A, Ai, pb = self.corr, self.A, self.Ai, self.arm.pose.bones
        G = {b: Ai @ pb[b].matrix @ corr[b].inverted() @ A for b in self.bones}
        out = {}
        for b in self.bones:
            pi = self.parent.get(self.by_name[b])
            pname = self.nodes[pi].get('name') if pi is not None else None
            out[b] = np.array(G[pname].inverted() @ G[b] if pname in G else G[b])
        return out

    def keep(self):
        ad, sc = self.ad, bpy.context.scene
        state = (ad.action, getattr(ad, 'action_slot', None), sc.frame_current)

        def restore():
            ad.action = state[0]
            if state[1] is not None and hasattr(ad, 'action_slot'):
                ad.action_slot = state[1]
            sc.frame_set(state[2])
        return restore


def sample_action(gs, act):
    """-> (frames, values {bone: {track: per frame}}, keys {bone: [frames with Blender keys]}):
    every joint's local transform in the game's space as the game's tracks (jointanim.decompose:
    Euler angles kept continuous), frames from 0 (or the action's first frame if negative) to its last."""
    import jointanim as JA
    import keyreduce as KR
    gs.use(act)
    f0, f1 = (int(act.frame_range[0]), int(act.frame_range[1]))
    f0 = 0 if f0 > 0 else f0
    keys = {}
    for fc in KR.action_fcurves(act):
        bone, _ = KR._bone_of(fc)
        if bone in gs.corr:
            keys.setdefault(bone, set()).update(int(round(kp.co.x)) - f0 for kp in fc.keyframe_points)
    vals = {b: {k: [] for k in JA.EDITABLE} for b in gs.bones}
    prev = {b: None for b in gs.bones}
    for fr in range(f0, f1 + 1):
        for b, L in gs.sample(fr).items():
            v = JA.decompose(L, prev[b])
            prev[b] = v
            for k in JA.EDITABLE:
                vals[b][k].append(float(v[k]))
    return f1 - f0 + 1, vals, {b: sorted(k) for b, k in keys.items()}
