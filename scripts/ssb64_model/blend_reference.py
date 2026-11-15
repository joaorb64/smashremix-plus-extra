"""Inside Blender (build_blend.py): the fighter .blend's 'Reference' collection (reference.py).

  Reference                        hidden; nothing in it is imported back
    Light item joint (joint_NN)    empties on the fighter's hold bones, at the game joint's frame
    Heavy item joint (joint_NN)
    Items > Ray Gun, Beam Sword...  each in its own collection: show the one to check
    Opponent                       Mario (armature 'Opponent'), with his grabbed / thrown animations
                                   (Opponent: CapturePulled, Thrown1...: pick one in its Action
                                   Editor; which ones a throw plays: the thrower's thrown_status)

Held items follow the hold joint like in the game: its matrix without scale (a Child Of
constraint, scale off), and 1 / attr->size (the game scales the fighter, not what it holds).
The opponent hangs from the heavy item joint like a grabbed / thrown fighter: turned like it,
its TransN joint on it (its animations' TransN moves are dropped, as the game cancels them).
"""
import json
import os
import sys

import bpy
import bmesh
import mathutils
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kirby_hat_gltf as KG  # noqa: E402

C = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)      # glTF (Y up) -> Blender (Z up)


def _coll(parent, name, hide=False):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    if hide:
        c.hide_render = True
    return c


def _lin(rgb255):
    return [((c / 255) / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4) for c in rgb255[:3]]


def _brighten(m, strength=0.6):
    """Reference materials: lit like the game's flat N64 look (emission of the base color)."""
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None) if nt else None
    if bsdf is None:
        return
    src = bsdf.inputs['Base Color'].links[0].from_socket if bsdf.inputs['Base Color'].links else None
    em = bsdf.inputs.get('Emission Color') or bsdf.inputs.get('Emission')
    if em is None:
        return
    if src is not None:
        nt.links.new(src, em)
    else:
        em.default_value = bsdf.inputs['Base Color'].default_value
    if 'Emission Strength' in bsdf.inputs:
        bsdf.inputs['Emission Strength'].default_value = strength


def _material(name, entry, folder):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m['n64_helper'] = True                          # (blend_to_gltf.py: not one of the model's materials)
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    pngs = entry.get('costumes') or []
    png = os.path.join(folder, pngs[0]) if pngs and pngs[0] else None
    if png and os.path.exists(png) and bsdf is not None:
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = bpy.data.images.load(png, check_existing=True)
        tex.location = (-400, 200)
        try:
            tex.extension = {'repeat': 'REPEAT', 'mirror': 'MIRROR'}.get((entry.get('wrap') or {}).get('s'), 'EXTEND')
        except TypeError:
            tex.extension = 'REPEAT'
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
        try:
            m.blend_method = 'CLIP'
        except (AttributeError, TypeError):
            pass
    elif bsdf is not None:
        col = entry.get('color')
        cc = (entry.get('costume_colors') or [{}])[0]
        if cc.get('prim'):
            col = [int(cc['prim'][i:i + 2], 16) for i in (0, 2, 4)]
        lin = _lin(col or [200, 200, 200])
        bsdf.inputs['Base Color'].default_value = (*lin, 1.0)
        m.diffuse_color = (*lin, 1.0)
    if bsdf is not None and 'Metallic' in bsdf.inputs:
        bsdf.inputs['Metallic'].default_value = 0.0
    _brighten(m)
    return m


def _mesh(name, m, mats, coll):
    """A mesh from kirby_hat_gltf.load_meshes data (game space), corners merged."""
    me = bpy.data.meshes.new(name)
    me.from_pydata((np.asarray(m['pos'], float) @ C.T).tolist(), [], [list(t) for _, t in m['tris']])
    order = list(dict.fromkeys(mat for mat, _ in m['tris']))
    for n in order:
        me.materials.append(mats[n])
    uv = me.uv_layers.new(name='UVMap')
    UV = np.asarray(m['uv'], float)
    for poly, (mat, _) in zip(me.polygons, m['tris']):
        poly.material_index = order.index(mat)
        for li in poly.loop_indices:
            v = me.loops[li].vertex_index
            uv.data[li].uv = (UV[v][0], 1.0 - UV[v][1])
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.update()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _game_to_blender(W):
    W = np.array(W, float)
    M = np.eye(4)
    M[:3, :3] = C @ W[:3, :3] @ C.T
    M[:3, 3] = C @ W[:3, 3]
    for k in range(3):                              # the held item's joint: rotation only (no joint scale)
        M[:3, k] /= np.linalg.norm(M[:3, k]) or 1
    return mathutils.Matrix(M.tolist())


def _hold_empty(name, info, arm, coll, size=1.0):
    """An empty in the game's hold-joint frame: the joint's matrix with its scale taken out
    (decomp func_ovl0_800C9A38), like a Child Of constraint without scale. The offset to the bone
    comes from the rest (bone rest vs the joint's rest world matrix), not from the current pose.
    The fighter is drawn at its model size, the game scales it by attr->size but not what it
    holds: the held frame gets 1 / size."""
    e = bpy.data.objects.new(name, None)
    e.empty_display_type = 'ARROWS'
    e.empty_display_size = 40.0
    coll.objects.link(e)
    G = _game_to_blender(info['world'])
    S = mathutils.Matrix.Diagonal((1 / size, 1 / size, 1 / size, 1.0))
    A = arm.matrix_world.copy() if arm is not None else mathutils.Matrix()
    if arm is not None and info['bone'] in arm.data.bones:
        R = (A @ arm.data.bones[info['bone']].matrix_local).normalized()
        R = mathutils.Matrix.LocRotScale(R.to_translation(), R.to_quaternion(), None)
        e.matrix_basis = A @ G @ S
        c = e.constraints.new('CHILD_OF')
        c.target = arm
        c.subtarget = info['bone']
        c.use_scale_x = c.use_scale_y = c.use_scale_z = False
        c.inverse_matrix = R.inverted()
        c.set_inverse_pending = False
    else:
        e.matrix_basis = A @ G @ S
    return e


def _drop_root_transform(path, meshes):
    """A held item is drawn without its root joint's transform: the item's creation ejects its
    root DObj (itManagerMakeItem: lbCommonEjectTreeDObj), and when held a new root (the hold
    joint's matrix) is put on top and the DObjDescs set again from it down (itMainSetFighterHold:
    gcSetDObjTransformsForGObj), so the root's desc lands on that new root, whose only matrix is
    the hold joint's. The Crate (root z -225, child +225) and the Barrel (-210 / +210) sit their
    child's offset away from the joint; the light items' roots are zero."""
    gl = json.load(open(path))
    nodes = gl.get('nodes', [])
    children = {c for nd in nodes for c in nd.get('children', [])}
    root = next((nd for i, nd in enumerate(nodes) if i not in children and nd.get('name') == 'joint_00'), None)
    if root is None:
        return
    inv = np.linalg.inv(KG._node_matrix(root))
    for m in meshes.values():
        P = np.asarray(m['pos'], float)
        m['pos'] = P @ inv[:3, :3].T + inv[:3, 3]


def build(export_dir, arm):
    ref = os.path.join(export_dir, 'reference')
    info = json.load(open(os.path.join(ref, 'reference.json')))
    scene = bpy.context.scene
    top = _coll(scene.collection, 'Reference', hide=True)
    holds = {}
    for key, label in (('light', 'Light item joint'), ('heavy', 'Heavy item joint')):
        if key in info.get('hold', {}):
            h = info['hold'][key]
            holds[key] = _hold_empty(f"{label} ({h['bone']})", h, arm, top, info.get('size') or 1.0)
    # ---- items, each in its hold joint's frame
    items = _coll(top, 'Items')
    for it in info.get('items', []):
        folder = os.path.join(ref, it['dir'])
        man = json.load(open(os.path.join(folder, 'manifest.json')))
        meshes = KG.load_meshes(os.path.join(folder, 'model.gltf'), names=None)
        _drop_root_transform(os.path.join(folder, 'model.gltf'), meshes)
        c = _coll(items, it['label'])
        mats = {}
        for mesh in meshes.values():
            for mat, _ in mesh['tris']:
                if mat not in mats:
                    mats[mat] = _material(f"ref_{it['name'].lower()}_{mat}", man['materials'].get(mat) or {}, folder)
        parent = holds.get(it['hold'])
        for mname, mesh in meshes.items():
            ob = _mesh(f"{it['label']} {mname}" if len(meshes) > 1 else it['label'], mesh, mats, c)
            if parent is not None:
                ob.parent = parent                  # (local identity: the item's model space = the joint's frame)
        if it is not info['items'][0]:
            lc = _find_layer_coll(bpy.context.view_layer.layer_collection, c.name)
            if lc is not None:
                lc.hide_viewport = True             # one item at a time (toggle them in the outliner)
    # ---- the opponent
    op = info.get('opponent')
    if op:
        _opponent(os.path.join(ref, op['dir']), op, holds.get('heavy'), _coll(top, 'Opponent'))
    lc = _find_layer_coll(bpy.context.view_layer.layer_collection, top.name)
    if lc is not None:
        lc.hide_viewport = True
    return top


def _find_layer_coll(lc, name):
    if lc.collection.name == name:
        return lc
    for c in lc.children:
        r = _find_layer_coll(c, name)
        if r is not None:
            return r
    return None


def _opponent(folder, op, hold, coll):
    man = json.load(open(os.path.join(folder, 'manifest.json')))
    before_obs, before_acts = set(bpy.data.objects), set(bpy.data.actions)
    before_mats = set(bpy.data.materials)
    bpy.ops.import_scene.gltf(filepath=os.path.join(folder, 'model.gltf'))
    new_obs = [o for o in bpy.data.objects if o not in before_obs]
    new_acts = [a for a in bpy.data.actions if a not in before_acts]
    for m in [m for m in bpy.data.materials if m not in before_mats]:
        entry = man['materials'].get(m.name) or {}
        m.name = f'ref_opponent_{m.name}'
        m['n64_helper'] = True
        cc = (entry.get('costume_colors') or [{}])[0]
        if entry.get('untextured') and cc.get('prim') and m.node_tree:
            for n in m.node_tree.nodes:
                if n.type == 'BSDF_PRINCIPLED':
                    n.inputs['Base Color'].default_value = (*_lin([int(cc['prim'][i:i + 2], 16) for i in (0, 2, 4)]), 1.0)
        _brighten(m)
    arm = next((o for o in new_obs if o.type == 'ARMATURE'), None)
    for o in new_obs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        if o.type == 'MESH' and not o.name.startswith('hi_'):
            bpy.data.objects.remove(o)              # (low detail, parts, skeleton: not needed)
            continue
        coll.objects.link(o)
        if o.type == 'MESH':
            o.name = f'Opponent {o.name}'
            o.hide_select = True
    if arm is None:
        return
    arm.name = 'Opponent'
    arm['n64_reference'] = True                     # (scripts looking for the fighter's armature skip it)
    arm.show_in_front = False
    # its actions: named after the animations, without TransN's moves (the game cancels them)
    by_gltf = {(info.get('gltf') or name): name for name, info in (man.get('fighter_animations') or {}).items()}
    first = None
    for act in new_acts:
        gname = next((g for g in by_gltf if act.name == g or act.name.startswith(g + '_') or act.name.startswith(g + '.')), None)
        act.name = f"Opponent: {by_gltf.get(gname, act.name)}"
        act['n64_reference'] = True
        act.use_fake_user = True
        for fc in [fc for fc in _fcurves(act) if fc.data_path.startswith('pose.bones["TransN"].location')]:
            _remove_fcurve(act, fc)
        if first is None or act.name.endswith(': Thrown1'):
            first = act                             # (shown: Thrown1, else the first one)
    ad = arm.animation_data_create()
    for tr in list(ad.nla_tracks):
        ad.nla_tracks.remove(tr)
    ad.action = first
    if hold is not None:
        # (ftCommonCapturePulledRotateScale) its TopN at -TransN * its size in the hold frame: its
        # TransN on the hold joint, scaled by its own size (the frame has none of the thrower's)
        arm.parent = hold
        arm.matrix_parent_inverse = mathutils.Matrix()
        sz = float(op.get('size') or 1.0)
        t = arm.data.bones['TransN'].head_local if 'TransN' in arm.data.bones else mathutils.Vector()
        arm.matrix_basis = mathutils.Matrix.Diagonal((sz, sz, sz, 1.0)) @ mathutils.Matrix.Translation(-t)
    print(f"reference opponent: {op['name']}, {len(new_acts)} animations"
          + (f' (showing {first.name})' if first else ''))


def _fcurves(act):
    try:
        return list(act.fcurves)
    except AttributeError:                          # Blender 5: slotted actions (layers / strips / channelbags)
        out = []
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out += list(bag.fcurves)
        return out


def _remove_fcurve(act, fc):
    try:
        act.fcurves.remove(fc)
        return
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    for layer in act.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                if fc in list(bag.fcurves):
                    bag.fcurves.remove(fc)
                    return
