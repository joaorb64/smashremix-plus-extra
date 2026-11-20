"""Inside Blender: the fighter armature's bones in the game's space (the joints' local matrices,
glTF Y up), and an action sampled into the game's per-frame joint tracks. Used by
blend_to_gltf.py (the import: edited animations) and anim_save.py (Save Animation .bin)."""
import json
import os
import re

import bpy


def fighter_armature():
    """The N64 fighter armature: the one named "fighter" (the export's), else the first armature
    carrying the fighter's custom properties (a copy of it, e.g. a retarget rig's CLONE, has them too)."""
    import bpy
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE' and 'n64_hidden_parts' in o.keys()]
    return next((o for o in arms if o.name == 'fighter'), arms[0] if arms else None)


class _GameSpace:
    """The fighter armature's bones in the game's space: sample(frame) -> {bone: its local 4x4
    matrix (numpy) as the game's joint (glTF Y up, the joint's own axes, relative to its parent
    joint)}. None when there's no fighter armature or no export model.gltf."""

    @classmethod
    def make(cls, export_dir):
        arm = fighter_armature()
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
        j_rests = {}
        for b in arm.data.bones:
            if b.name in self.by_name:
                j_rest = (self.A @ node_world(self.by_name[b.name]) @ self.Ai).to_3x3().normalized()
                j_rests[b.name] = j_rest
                self.corr[b.name] = (j_rest.inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
        # joints added in Blender (Bone > N64 Joint > Add Joint; not in the export): the import gives
        # them their parent's axes at their own position, so their rest axes are their parent's
        self.added_parent = {}
        for b in arm.data.bones:                   # (parents come before children)
            if b.name in self.corr or not re.match(r'joint_\d+$', b.name) or b.parent is None:
                continue
            if b.parent.name not in j_rests:
                continue
            j_rests[b.name] = j_rests[b.parent.name]
            self.corr[b.name] = (j_rests[b.name].inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
            self.added_parent[b.name] = b.parent.name
        self.bones = [b for b in self.corr if re.match(r'joint_\d+$|TransN$|XRotN$|YRotN$', b)]
        # A game joint scales only along its own axes; the bones' axes needn't be those (the armature's
        # bones point at their children: bone_heuristic TEMPERANCE). A non-uniform bone scale is stored
        # along the game axes nearest to the bone axes it was keyed on (sample), not as the oblique
        # squash (shear) the game can't play. snap[b][k] = the bone axis that game axis k takes the
        # scale of (Blender-converted joint axes); aligned[b] = how well they line up (1 = exactly).
        self.snap, self.aligned, self.snapped = {}, {}, set()
        for b in self.bones:
            ci = self.corr[b].to_3x3().inverted()           # column k: joint axis k in bone axes
            self.snap[b] = [max(range(3), key=lambda i: abs(ci[i][k])) for k in range(3)]
            self.aligned[b] = min(abs(ci[self.snap[b][k]][k]) for k in range(3))
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
            bp = self.arm.data.bones[b].parent
            if bp is not None and bp.name in G:
                pname = bp.name            # (Blender's parent: a joint moved to another parent follows it)
            elif b in self.added_parent:
                pname = self.added_parent[b]
            else:
                pi = self.parent.get(self.by_name[b])
                pname = self.nodes[pi].get('name') if pi is not None else None
            out[b] = np.array(G[pname].inverted() @ G[b] if pname in G else G[b])
        from mathutils import Matrix
        for b in self.bones:
            s = pb[b].scale
            if max(s) - min(s) < 1e-6 or min(abs(c) for c in s) < 1e-9:
                continue                                    # uniform: the same along any axes
            d = [s[i] for i in self.snap[b]]
            if all(abs(d[k] - s[k]) < 1e-9 for k in range(3)) and self.aligned[b] > 0.9999:
                continue                                    # bone axes = joint axes: already exact
            # local = Ai corr_p rest T R S corr_b^-1 A  ->  Ai corr_p rest T R corr_b^-1 D A
            C = corr[b]
            fix = Ai @ C @ Matrix.Diagonal((1 / s[0], 1 / s[1], 1 / s[2], 1.0)) @ C.inverted() @ \
                Matrix.Diagonal((*d, 1.0)) @ A
            out[b] = out[b] @ np.array(fix)
            if self.aligned[b] < 0.999:
                self.snapped.add(b)
        return out

    def keep(self):
        """Save the armature's state for sampling actions; -> restore(). While sampling, the
        armature's bone constraints are muted: an action is sampled as its own keys play it (e.g. a
        retarget rig's constraints, which make the armature follow another one live, would otherwise
        replace every action with that other armature's current motion)."""
        ad, sc = self.ad, bpy.context.scene
        state = (ad.action, getattr(ad, 'action_slot', None), sc.frame_current)
        muted = [c for pb in self.arm.pose.bones for c in pb.constraints if not c.mute]
        for c in muted:
            c.mute = True

        def restore():
            for c in muted:
                c.mute = False
            ad.action = state[0]
            if state[1] is not None and hasattr(ad, 'action_slot'):
                ad.action_slot = state[1]
            sc.frame_set(state[2])
        return restore


def key_range(act):
    """(first, last) frame the animation plays: its keyframes' extent (from 0, or the first key if
    negative), not the action's manual frame range (set at import: it doesn't follow edited keys).
    The manual range is updated to it, so the timeline shows what's exported. A single pose (keys
    on one frame only) keeps its manual range."""
    import keyreduce as KR
    xs = [kp.co.x for fc in KR.action_fcurves(act) for kp in fc.keyframe_points]
    if not xs or round(min(xs)) == round(max(xs)):
        # no keys / a single pose: only the manual range gives its length (menu poses, Shield Neutral)
        return min(int(act.frame_range[0]), 0), int(act.frame_range[1])
    else:
        f0, f1 = int(round(min(xs))), int(round(max(xs)))
    f0 = min(f0, 0)
    f1 = max(f1, f0 + 1)
    try:
        if getattr(act, 'use_frame_range', False) and (int(act.frame_start), int(act.frame_end)) != (f0, f1):
            act.frame_start, act.frame_end = f0, f1
    except (AttributeError, RuntimeError):
        pass
    return f0, f1


def sample_action(gs, act):
    """-> (frames, values {bone: {track: per frame}}, keys {bone: [frames with Blender keys]}):
    every joint's local transform in the game's space as the game's tracks (jointanim.decompose:
    Euler angles kept continuous), frames from 0 (or the action's first frame if negative) to its last."""
    import jointanim as JA
    import keyreduce as KR
    gs.use(act)
    f0, f1 = key_range(act)
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
