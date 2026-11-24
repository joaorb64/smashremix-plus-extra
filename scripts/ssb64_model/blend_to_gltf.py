"""Export a model.blend (made by build_blend.py, then edited) for import_fighter.py /
import_object.py / import_stage.py (ssb64_model.py import runs both steps). Run inside Blender:

  blender -b model.blend --python blend_to_gltf.py -- OUT_DIR

Writes OUT_DIR/body.gltf ('Costume 0' collection = hi detail), lo.gltf
('Low detail'), parts.gltf ('Model parts') and costumes.json: for every
material, the image (or flat color) each costume uses, read from the
'Costume N' collections, so whatever is painted / assigned in Blender is what
gets built; plus each material's n64_* properties and where the export
(manifest.json, textures/) is. Geometry is shared by all costumes; only costume 0's is exported.
"""
import json
import os
import re
import sys

import bpy

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = os.path.abspath(args[0])
os.makedirs(OUT, exist_ok=True)
BLEND_DIR = os.path.dirname(bpy.data.filepath)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matanim as MA  # noqa: E402
import n64_panel as PANEL  # noqa: E402
PANEL.reset_previews()      # costume / expression / model part previews: back to what's read
import blend_gamedata as GDB  # noqa: E402
from blend_gamespace import sample_action  # noqa: E402
import blend_gamespace as BGS  # noqa: E402
import blend_joints as BJ  # noqa: E402
# fighters: joints removed / renumbered in Blender (Bone > N64 Joint > Remove Joint): everything
# back to the export's joint names (in memory) - the import renumbers the files
_FIGHTER_ARM = BJ.fighter_armature()
_FIGHTER_JOINTS = BJ.restore_export_names(_FIGHTER_ARM) if _FIGHTER_ARM is not None else None


def image_path(img):
    if not img.filepath:                                # made in Blender, no file yet: one in OUT
        path = os.path.join(OUT, 'images', re.sub(r'[^\w.-]', '_', img.name) + '.png')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.filepath_raw, img.file_format = path, 'PNG'
        img.save()
        return path
    if img.is_dirty or img.packed_file is not None:     # painted in Blender, not saved / a packed copy:
        img.save()                                      # its file gets what Blender shows
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library))


def anim_info(m):
    """A material's animation (matanim.py tracks: the n64_offset_x / n64_tex_index / ...
    properties): keys per track [frame, value, interpolation, slope in, slope out] (colors:
    RGBA words), the values at every frame 0..frames (what the build checks the script
    against), loop, the starting values of the properties that aren't animated, and its
    image list ('N64 Frame <i>' nodes). None: nothing to say."""
    props = PANEL.anim_props(m)
    frames_imgs = [image_path(n.image) for n in PANEL.frame_nodes(m) if n.image]
    pal_imgs = [image_path(n.image) for n in PANEL.frame_nodes(m, 'Palette') if n.image]
    if not props and not frames_imgs and not pal_imgs and 'n64_mobj' not in m.keys():
        return None
    animated = PANEL.anim_animated(m)

    def pack(vals):
        return sum(max(0, min(255, round(float(v) * 255))) << sh for v, sh in zip(vals, (24, 16, 8, 0)))
    static = {}
    for p in props:
        if p not in animated:
            v = m[p]
            static[MA.TRACK_OF_PROP[p]] = pack(v) if PANEL.ANIM_INFO[p][2] == 'color' else float(v)
    last = max((kp.co.x for fcs in animated.values() for fc in fcs for kp in fc.keyframe_points), default=0)
    end = int(m.get('n64_anim_frames', round(last)) or round(last))
    cycles = any(md.type == 'CYCLES' for fcs in animated.values() for fc in fcs for md in fc.modifiers)
    loop = bool(m.get('n64_anim_loop', cycles))
    if loop and cycles:      # Blender repeats each curve's key span; the game the animation's length
        for p, fcs in animated.items():
            span = {(round(fc.keyframe_points[0].co.x), round(fc.keyframe_points[-1].co.x)) for fc in fcs}
            if any(sp != (0, end) for sp in span):
                print(f'  warning: {m.name}: {p} keys span frames {sorted(span)[0]} but the loop is 0..{end}: '
                      f'the game repeats every {end} frames (key frames 0 and {end} for it to look the same)')
    tracks, samples = {}, {}
    for p, fcs in animated.items():
        track = MA.TRACK_OF_PROP[p]
        if PANEL.ANIM_INFO[p][2] == 'color':
            by = {fc.array_index: fc for fc in fcs}
            base = list(m[p])

            def at(f, _by=by, _base=base):
                return pack([_by[i].evaluate(f) if i in _by else _base[i] for i in range(4)])
            times = sorted({int(round(kp.co.x)) for fc in fcs for kp in fc.keyframe_points})
            keys = []
            for t in times:          # a step only if every channel holds there
                ips = [next((kp.interpolation for kp in reversed(fc.keyframe_points) if kp.co.x <= t + 1e-3),
                            'CONSTANT') for fc in fcs]
                keys.append([t, at(t), 'CONSTANT' if all(i == 'CONSTANT' for i in ips) else 'LINEAR', 0.0, 0.0])
            tracks[track] = keys
            samples[track] = [at(f) for f in range(end + 1)]
            continue
        fc = fcs[0]
        disc = track in MA.DISCRETE
        keys = []
        for kp in fc.keyframe_points:
            (x, y), (lx, ly), (rx, ry) = kp.co, kp.handle_left, kp.handle_right
            ip = kp.interpolation if kp.interpolation in ('CONSTANT', 'LINEAR') else 'BEZIER'
            s_in = (y - ly) / (x - lx) if abs(x - lx) > 1e-6 else 0.0
            s_out = (ry - y) / (rx - x) if abs(rx - x) > 1e-6 else 0.0
            keys.append([int(round(x)), float(round(y) if disc else y), 'CONSTANT' if disc else ip, s_in, s_out])
        tracks[track] = keys
        samples[track] = [float(round(fc.evaluate(f)) if disc else fc.evaluate(f)) for f in range(end + 1)]
    return dict(tracks=tracks, samples=samples, frames=end, loop=loop,
                loop_start=int(m.get('n64_anim_loop_start', 0)) if loop else 0,
                static=static, images=frames_imgs, palettes=pal_imgs, mobj=(m['n64_mobj'].to_dict() if 'n64_mobj' in m.keys() else None))


def mat_info(m):
    if m is None:
        return None
    img, color = None, None
    if m.node_tree:
        for node in m.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image and not node.name.startswith('N64 '):
                img = os.path.normpath(bpy.path.abspath(node.image.filepath, library=node.image.library))
                if node.image.is_dirty:                     # painted in Blender, not saved: save next to the blend
                    node.image.save()
            if node.type == 'BSDF_PRINCIPLED':
                c = node.inputs['Base Color'].default_value
                color = [float(c[0]), float(c[1]), float(c[2])]
    return dict(image=img, color=color)


def base_name(name):
    return re.sub(r'(_c\d+)?(\.\d+)?$', '', name)


costumes = {}
for coll in bpy.data.collections:
    m = re.match(r'Costume (\d+)$', coll.name)
    if not m:
        continue
    k = int(m.group(1))
    for ob in coll.objects:
        if ob.type != 'MESH':
            continue
        for slot in ob.material_slots:
            if slot.material is not None and slot.material.get('n64_helper'):
                continue
            info = mat_info(slot.material)
            if info and slot.material:
                costumes.setdefault(base_name(slot.material.name), {})[k] = info
# materials only used outside the costume collections (low detail, model parts): costume k's
# image is the file the export gave it (manifest), or the one Add Costume picked (n64_costume_files);
# older exports: the costume-0 image's sibling <mat>_c<k>.png
_exp_dir = os.path.normpath(bpy.path.abspath(bpy.context.scene.get('n64_export_dir',
                                                                     bpy.context.scene.get('fighter_export_dir', '//'))))
try:
    _man_mats = json.load(open(os.path.join(_exp_dir, 'manifest.json'))).get('materials') or {}
except (OSError, ValueError):
    _man_mats = {}
for m in bpy.data.materials:
    name = base_name(m.name)
    if name in costumes or m.users == 0 or m.get('n64_helper'):
        continue
    info = mat_info(m)
    costumes[name] = {0: info}
    img = info.get('image') if info else None
    for k, rel in enumerate((_man_mats.get(name) or {}).get('costumes') or []):
        if k and rel and os.path.exists(os.path.join(_exp_dir, rel)):
            costumes[name][k] = dict(image=os.path.join(_exp_dir, rel), color=info.get('color'))
    if img and re.search(r'_c0\.png$', img):
        for k in range(1, 32):
            sib = re.sub(r'_c0\.png$', f'_c{k}.png', img)
            if not os.path.exists(sib):
                break
            costumes[name][k] = dict(image=sib, color=info.get('color'))
# costumes added in Blender (N64 Fighter > Add Costume): every material's file for them
for m in bpy.data.materials:
    if 'n64_costume_files' in m.keys() and not m.get('n64_helper'):
        name = base_name(m.name)
        for k, path in dict(m['n64_costume_files']).items():
            if int(k) not in costumes.setdefault(name, {}) and os.path.exists(bpy.path.abspath(path)):
                costumes[name][int(k)] = dict(image=os.path.normpath(bpy.path.abspath(path)))
# how many costumes the .blend has (Costume N collections): the import writes that many
COSTUME_COUNT = len([c for c in bpy.data.collections if re.match(r'Costume \d+$', c.name)]) or None
# N64 settings (custom properties) per material; costume copies inherit costume 0's
props, anims = {}, {}
for m in bpy.data.materials:
    name = base_name(m.name)
    if (name in props and m.name != name) or m.get('n64_helper'):
        continue
    p = {k: (m[k] if isinstance(m[k], str) else str(m[k])) for k in ('n64_format', 'n64_wrap_s', 'n64_wrap_t',
                                                                      'n64_cull', 'n64_lighting', 'n64_light_color',
                                                                      'n64_shadow_color', 'n64_state') if k in m.keys()}
    # culling: Blender's Backface Culling checkbox (on: back faces culled, off: double-sided);
    # n64_cull only for what it can't say (front / both)
    if p.get('n64_cull') not in ('front', 'both'):
        p['n64_cull'] = 'back' if m.use_backface_culling else 'none'
    a = anim_info(m)
    if a is not None:
        anims[name] = a
        if a['images']:
            p['n64_frames'] = a['images']
        if a['palettes']:
            p['n64_palettes'] = a['palettes']
        if a['tracks']:
            p['n64_animated'] = sorted(a['tracks'])
    if 'n64_image_order' in m.keys():        # alternate textures removed / moved (N64 sidebar)
        p['n64_image_order'] = [int(x) for x in m['n64_image_order']]
        p['n64_image_known'] = int(m.get('n64_image_known', 0))       # (entries past it: appended)
    if p:
        props[name] = p
# images painted in Blender but not saved yet (expressions, palettes)
for img in bpy.data.images:
    if img.is_dirty and img.filepath:
        img.save()
export_dir = os.path.normpath(bpy.path.abspath(bpy.context.scene.get('n64_export_dir', bpy.context.scene.get('fighter_export_dir', '//'))))
sc = bpy.context.scene
lights = {k: str(sc[f'n64_{k}_color']) for k in ('light', 'shadow') if f'n64_{k}_color' in sc.keys()}


def export_joint_names():
    """The joint (bone) names the export's model.gltf has (a stage's original joints)."""
    gl_path = os.path.join(export_dir, 'model.gltf')
    if not os.path.exists(gl_path):
        return set()
    return {n.get('name') for n in json.load(open(gl_path))['nodes']}


def stage_orig_name(b, have):
    """The export's name for a stage joint bone's original joint (its bone may have been renumbered:
    n64_joint_orig, stamped by the N64 Stage panel before its first edit), None for a new joint."""
    m = re.match(r'(L\d+)_joint_(\d+)$', b.name)
    if not m:
        return b.name if b.name in have else None
    if 'n64_joint_orig' in b.keys():
        o = int(b['n64_joint_orig'])
        return f'{m.group(1)}_joint_{o:02d}' if o >= 0 else None
    return b.name if b.name in have and not b.get('n64_new_joint') else None


def stage_parent_index(b):
    """A stage joint bone's parent joint (its parent bone when that is a joint of its layer, else the
    layer's root; the root: None)."""
    m = re.match(r'(L\d+)_joint_(\d+)$', b.name)
    if int(m.group(2)) == 0:
        return None
    p = re.match(rf'{m.group(1)}_joint_(\d+)$', b.parent.name) if b.parent is not None else None
    return int(p.group(1)) if p else 0


def joint_map():
    """Stages: every layer's joints as Blender has them -> {layer: [{index, orig, parent}]}: orig = the
    export's joint it is (None: a new joint), parent = its parent joint. A layer the export had and
    Blender has no bones for left: removed ([]). import_stage.py rebuilds the layers' joints from it."""
    if bpy.context.scene.get('n64_kind') != 'stage':
        return None
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and not o.get('n64_reference')), None)
    have = export_joint_names()
    layers = {m.group(1) for n in have for m in [re.match(r'(L\d+)_joint_\d+$', n or '')] if m}
    out = {L: [] for L in layers}
    for b in (arm.data.bones if arm else []):
        m = re.match(r'(L\d+)_joint_(\d+)$', b.name)
        if not m:
            continue
        o = stage_orig_name(b, have)
        out.setdefault(m.group(1), []).append(dict(index=int(m.group(2)), orig=int(o.split('_')[-1]) if o else None,
                                                   parent=stage_parent_index(b)))
    for rows in out.values():
        rows.sort(key=lambda r: r['index'])
    return out


def fighter_joints():
    """Fighters: the export's joints that have no bone anymore (removed) -> dict(removed=[DObjDesc
    indices]); import_fighter.py renumbers everything indexed by joint (jointmap.py)."""
    if _FIGHTER_ARM is None:
        return None
    have = {int(m.group(1)) for n in export_joint_names() for m in [re.match(r'joint_(\d+)$', n or '')] if m}
    now = {BJ.joint_index(b.name) for b in _FIGHTER_ARM.data.bones} - {None}
    gone = sorted(have - now)
    if gone:
        print('fighter joints removed: ' + ', '.join(f'joint_{i:02d}' for i in gone))
    info = dict(removed=gone)
    fj = _FIGHTER_JOINTS or {}
    # every joint's parent now (export / temporary numbers; None: not under a joint) against the export's
    parents = {}
    for b in _FIGHTER_ARM.data.bones:
        i = BJ.joint_index(b.name)
        if i is not None:
            parents[i] = BJ.joint_index(b.parent.name) if b.parent is not None else None
    try:
        man_joints = json.load(open(os.path.join(export_dir, 'manifest.json'))).get('joints') or []
    except (OSError, ValueError):
        man_joints = []
    moved = {j['index']: parents[j['index']] for j in man_joints
             if j['index'] in parents and parents[j['index']] != j.get('parent')}
    if moved:
        print('fighter joints moved: ' + ', '.join(f'joint_{x:02d} -> under joint_{p:02d}' for x, p in moved.items()))
    if fj.get('added') or moved or [x for x in fj.get('order', []) if x in have] != sorted(have - set(gone)):
        modes = {str(BJ.joint_index(b.name)): str(b.get('n64_reparent_mode', 'FOLLOW'))
                 for b in _FIGHTER_ARM.data.bones if BJ.joint_index(b.name) in moved}
        info.update(order=fj['order'], added={str(k): v for k, v in fj['added'].items()},
                    parents={str(k): v for k, v in parents.items()}, moved_modes=modes)
        if fj.get('added'):
            print(f"fighter joints added: {len(fj['added'])}")
    return info


def new_joints():
    """Stages: bones L<n>_joint_NN the export didn't have (new joints) -> {layer: {NN: parent NN}}: the
    parent is the bone's parent when that is a joint of the same layer, else the layer's root (0)."""
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and not o.get('n64_reference')), None)
    have = export_joint_names()
    out = {}
    for b in (arm.data.bones if arm else []):
        m = re.match(r'(L\d+)_joint_(\d+)$', b.name)
        if not m or b.name in have:
            continue
        p = re.match(rf'{m.group(1)}_joint_(\d+)$', b.parent.name) if b.parent else None
        out.setdefault(m.group(1), {})[m.group(2).lstrip('0') or '0'] = int(p.group(1)) if p else 0
    return out


def joint_anims():
    """Stages: each animated joint's local transform per frame (bones L<n>_joint_NN that have
    a game animation: n64_anim_frames, or keyframes), over its own length -> {layer: {joint:
    dict(frames, loop, loop_start, locked, matrices: [4x4 row-major per frame 0..frames])}}, in
    the game's space (glTF's, Y up). import_stage.py makes the scripts from them."""
    from mathutils import Matrix, Quaternion
    gl_path = os.path.join(export_dir, 'model.gltf')
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and
                any(re.match(r'(L\d+_)?joint_\d+$', b.name) for b in o.data.bones)), None)
    if arm is None or not os.path.exists(gl_path) or not bpy.context.scene.get('n64_kind'):
        return None
    gl = json.load(open(gl_path))
    nodes = gl['nodes']
    parent = {c: i for i, n in enumerate(nodes) for c in n.get('children', [])}
    by_name = {n.get('name'): i for i, n in enumerate(nodes)}

    def local(n):
        t = n.get('translation', [0, 0, 0])
        q = n.get('rotation', [0, 0, 0, 1])
        sc = n.get('scale', [1, 1, 1])
        return Matrix.Translation(t) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4() @ \
            Matrix.Diagonal((*sc, 1.0))
    world = {}

    def node_world(i):
        if i not in world:
            world[i] = (node_world(parent[i]) @ local(nodes[i])) if i in parent else local(nodes[i])
        return world[i]
    A = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))     # glTF (Y up) -> Blender
    Ai = A.inverted()
    corr = {}                      # per bone: its rest axes vs the joint's (the importer's bone axes): a
    have = set(by_name)
    for b in arm.data.bones:       # rotation only (bones have no rest scale: a joint's is in its pose)
        o = stage_orig_name(b, have)                     # (stage joints: renumbered ones by their original)
        if o in by_name:
            j_rest = (A @ node_world(by_name[o]) @ Ai).to_3x3().normalized()
            corr[b.name] = (j_rest.inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
        elif re.match(r'L\d+_joint_\d+$', b.name):       # a new stage joint: no rest transform
            corr[b.name] = b.matrix_local.to_3x3().normalized().to_4x4()
    fcs = PANEL.anim_fcurves(arm) or []
    keyed = {m.group(1) for fc in fcs for m in [re.match(r'pose\.bones\["([^"]+)"\]', fc.data_path)] if m}
    todo = {}
    for pb in arm.pose.bones:
        m = re.match(r'(?:(L\d+)_)?joint_(\d+)$', pb.name)          # (items, projectiles: joint_NN)
        if not m or pb.name not in corr or ('n64_anim_frames' not in pb.keys() and pb.name not in keyed):
            continue
        last = max((kp.co.x for fc in fcs if fc.data_path.startswith(f'pose.bones["{pb.name}"]')
                    for kp in fc.keyframe_points), default=0)
        frames = int(pb.get('n64_anim_frames', round(last)))
        loop = bool(pb.get('n64_anim_loop', any(md.type == 'CYCLES' for fc in fcs
                                                if fc.data_path.startswith(f'pose.bones["{pb.name}"]')
                                                for md in fc.modifiers)))
        todo[pb.name] = dict(layer=m.group(1) or 'hi', joint=int(m.group(2)), frames=frames, loop=loop,
                             loop_start=int(pb.get('n64_anim_loop_start', 0)) if loop else 0,
                             locked=bool(pb.get('n64_anim_locked', False)), matrices=[])
    if not todo:
        return {}
    sc = bpy.context.scene
    keep_frame = sc.frame_current
    for f in range(max(t['frames'] for t in todo.values()) + 1):
        sc.frame_set(f)
        for name, t in todo.items():
            if f > t['frames']:
                continue
            pb = arm.pose.bones[name]

            def game(pbone):                 # the joint's world matrix in glTF space this frame
                return Ai @ pbone.matrix @ corr[pbone.name].inverted() @ A
            N = game(pb)
            sm = re.match(r'(L\d+)_joint_\d+$', name)
            if sm:                           # a stage joint: relative to its parent joint (as Blender has it)
                pi = stage_parent_index(arm.data.bones[name])
                pname = f'{sm.group(1)}_joint_{pi:02d}' if pi is not None else None
            else:
                pi = parent.get(by_name[name])
                pname = nodes[pi].get('name') if pi is not None else None
            if pname in arm.pose.bones and pname in corr:
                N = game(arm.pose.bones[pname]).inverted() @ N
            t['matrices'].append([v for row in N for v in row])
    sc.frame_set(keep_frame)
    out = {}
    for t in todo.values():
        out.setdefault(t.pop('layer'), {})[str(t.pop('joint'))] = t
    return out


# ssb64_model.py import --all: every animation with its own file (animations/*.bin) is written, edited
# or not (the base character's borrowed ones still only when edited: each would become a new file)
ALL_ANIMS = os.environ.get('N64_IMPORT_ALL') == '1'


def fighter_anims():
    """Fighter animations edited since the export (keys, flags or loop changed: build_blend.py
    stored each one's state in n64_export) -> [dict(name, export_name, flags, loop, frames, keys
    {bone: [frames]} (where the Blender keys are), values {bone: {track: per frame}})]: every
    joint's local transform in the game's space (glTF Y up, the joint's own axes), as the game's
    tracks (jointanim.decompose: Euler angles kept continuous). import_fighter.py writes them."""
    import keyreduce as KR
    todo = []
    for act in bpy.data.actions:
        ex = act.get('n64_export')
        if ex is None or 'n64_flags' not in act.keys():
            continue
        ex = ex.to_dict() if hasattr(ex, 'to_dict') else dict(ex)
        flags = str(act['n64_flags']).upper()
        loop = bool(act.get('n64_loop', False))
        own_file = str(act.get('n64_source') or '').startswith('animations/')
        if (KR.action_signature(act) == ex.get('keys') and flags == str(ex.get('flags')).upper()
                and loop == bool(ex.get('loop'))) and not (ALL_ANIMS and own_file):
            continue
        todo.append((act, ex, flags, loop))
    gs = BGS._GameSpace.make(export_dir) if todo else None
    if gs is None:
        return []
    restore = gs.keep()
    out = []
    for act, ex, flags, loop in todo:
        frames, vals, keys = sample_action(gs, act)
        out.append(dict(name=act.name, export_name=ex.get('name'), flags=flags, loop=loop,
                        flags_before=ex.get('flags'), frames=frames, keys=keys, values=vals))
        print(f'fighter animation {"written" if ALL_ANIMS else "changed"}: {act.name} ({frames} frames, flags {flags})')
    restore()
    return out


def shield_pose():
    """The shield pose actions ('Shield': frame = stick angle 0..360; 'Shield Neutral': its
    frame 0) -> dict(turn {bone: [local 4x4 per frame, row-major]}, neutral {bone: 4x4},
    radius (the Shield Bubble's), keys {bone: [frames with Blender keys]}), or None.
    import_fighter.py compares it with the shield-pose file and writes it when it changed."""
    import keyreduce as KR
    turn_act, neu_act = bpy.data.actions.get('Shield'), bpy.data.actions.get('Shield Neutral')
    if turn_act is None or neu_act is None:
        return None
    gs = BGS._GameSpace.make(export_dir)
    if gs is None:
        return None
    restore = gs.keep()
    n = int(bpy.context.scene.get('n64_shield_frames', 361))
    turn = {b: [] for b in gs.bones}
    gs.use(turn_act)
    for fr in range(n):
        for b, L in gs.sample(fr).items():
            turn[b].append([round(float(x), 7) for x in L.flatten()])
    keys = {}
    for fc in KR.action_fcurves(turn_act):
        bone, _ = KR._bone_of(fc)
        if bone in gs.corr:
            keys.setdefault(bone, set()).update(int(round(kp.co.x)) for kp in fc.keyframe_points)
    gs.use(neu_act)
    neutral = {b: [round(float(x), 7) for x in L.flatten()] for b, L in gs.sample(0).items()}
    # the bones edited since the export (keys changed); None: unknown (an older .blend)
    edited = None
    if 'n64_bone_signatures' in turn_act.keys() and 'n64_bone_signatures' in neu_act.keys():
        edited = []
        for act, tag in ((turn_act, 'turn'), (neu_act, 'neutral')):
            was = dict(act['n64_bone_signatures'])
            now = KR.bone_signatures(act)
            edited += [[tag, b] for b in sorted(set(was) | set(now)) if was.get(b) != now.get(b)]
    restore()
    bub = bpy.data.objects.get('Shield Bubble')
    radius = None
    if bub is not None:
        radius = float(bub.empty_display_size) * max(abs(x) for x in bub.scale)
    return dict(frames=n, turn=turn, neutral=neutral, radius=radius, edited=edited,
                keys={b: sorted(k) for b, k in keys.items()})


# hurtboxes / stage collision (gamedata.py applies them)
open(os.path.join(OUT, 'gamedata.json'), 'w').write(json.dumps(GDB.collect(), indent=1))
json.dump(dict(export_dir=export_dir, materials=costumes, props=props, lights=lights, anims=anims,
               joint_anims=joint_anims(), new_joints=new_joints(), joint_map=joint_map(), fighter_joints=fighter_joints(),
               fighter_anims=fighter_anims(), costume_count=COSTUME_COUNT,
               # model part variants renumbered in Blender (removed / moved): object -> its export variant
               part_origins={base_name(o.name): int(o['n64_part_orig']) for o in bpy.data.objects
                             if o.type == 'MESH' and 'n64_part_orig' in o.keys()},
               costume_sources={str(k): int(v) for k, v in dict(bpy.context.scene.get('n64_costume_sources') or {}).items()},
               shield_pose=shield_pose()),
          open(os.path.join(OUT, 'costumes.json'), 'w'), indent=1)

# the N64 view setup (unlit = emission x vertex color node) only exists for looking at it
# in Blender, and confuses the glTF exporter's vertex colors: plain shaders for the export
# (in memory; the .blend isn't saved)
for m in bpy.data.materials:
    nt = m.node_tree
    if nt is None:
        continue
    out = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None)
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if out is not None and bsdf is not None:
        nt.links.new(bsdf.outputs[0], out.inputs['Surface'])
    for n in [n for n in nt.nodes if n.name.startswith('N64 ')]:
        nt.nodes.remove(n)

# (.blend files built with the old layer-order modifier: it only moved layers for viewing)
for ob in bpy.data.objects:
    for md in [md for md in ob.modifiers if md.name == 'N64 Layer Order']:
        ob.modifiers.remove(md)

def _export_zero_color_objects():
    """The export's objects (model.gltf) that have corners with color (0, 0, 0, 0): those are the
    game's (Meta Knight's skeleton); anywhere else such a corner is Blender's fill for geometry
    joined in from a mesh without the color attribute."""
    import numpy as np
    gl_path = os.path.join(export_dir, 'model.gltf')
    if not os.path.exists(gl_path):
        return set()
    gl = json.load(open(gl_path))
    bufs, out = {}, set()
    for nd in gl['nodes']:
        for prim in gl['meshes'][nd['mesh']]['primitives'] if 'mesh' in nd else []:
            i = prim['attributes'].get('COLOR_0')
            a = gl['accessors'][i] if i is not None else None
            if a is None or a['type'] != 'VEC4':
                continue
            bv = gl['bufferViews'][a['bufferView']]
            uri = gl['buffers'][bv['buffer']]['uri']
            raw = bufs.setdefault(uri, open(os.path.join(export_dir, uri), 'rb').read())
            dt = np.dtype('<' + {5126: 'f4', 5123: 'u2', 5121: 'u1'}[a['componentType']])
            stride = bv.get('byteStride', 0) or dt.itemsize * 4
            start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
            if any(not np.frombuffer(raw, dt, 4, start + k * stride).any() for k in range(a['count'])):
                out.add(base_name(nd.get('name', '')))
    return out


EXPORTED_COLLECTIONS = ('Costume 0', 'Low detail', 'Model parts', 'Electric skeleton')
ZERO_COLOR_OBJECTS = _export_zero_color_objects()
for me in {o.data for o in bpy.data.objects if o.type == 'MESH'}:      # (costumes share meshes)
    names = sorted({base_name(o.name) for o in bpy.data.objects if o.data == me})
    # UV maps: the glTF's TEXCOORD_0 is the first one. A mesh joined in with a differently named
    # UV map brings its own map, and its faces are all (0, 0) in the first: one texel of the
    # texture in game. Faces with nothing in the first map take the other maps' UVs; one map left.
    uvs = [u for u in me.uv_layers if not u.name.startswith('N64_')]
    if len(uvs) > 1:
        main = uvs[0]
        moved = 0
        for poly in me.polygons:
            lis = list(poly.loop_indices)
            if any(main.data[li].uv.length_squared > 0 for li in lis):
                continue
            src = next((u for u in uvs[1:] if any(u.data[li].uv.length_squared > 0 for li in lis)), None)
            if src is not None:
                for li in lis:
                    main.data[li].uv = src.data[li].uv
                moved += 1
        print(f"  note: {', '.join(names)}: {len(uvs)} UV maps ({', '.join(u.name for u in uvs)}): the game has one; "
              f"using {main.name!r}" + (f', {moved} faces without UVs in it took theirs from the others' if moved else ''))
        for u in uvs[1:]:
            me.uv_layers.remove(me.uv_layers[u.name])
    attr = me.color_attributes.active_color or (me.color_attributes[0] if me.color_attributes else None)
    if attr is None or 'N64_RG' in me.uv_layers:
        continue
    if len(me.uv_layers) > 6:
        print(f'  warning: {me.name}: no room for the vertex color UV maps (8 max); its colors use COLOR_0')
        continue
    # read the colors before adding the UV maps: adding attributes reallocates the mesh's
    # attribute storage, which leaves `attr` (and its .data) dangling (IndexError, size 0)
    corner = attr.domain == 'CORNER'
    if len(attr.data) == 0:
        continue
    cols = [tuple(attr.data[li if corner else loop.vertex_index].color_srgb)
            for li, loop in enumerate(me.loops)]
    # geometry joined in from a mesh without this color attribute gets (0, 0, 0, 0): black and
    # see-through on unlit materials (texture x vertex color) - white, as the vertex had no color
    if not ZERO_COLOR_OBJECTS & set(names):
        blank = [li for li, c in enumerate(cols) if not any(c)]
        if blank:
            print(f"  note: {', '.join(names)}: {len(blank)} corners have no vertex color (0, 0, 0, 0: geometry "
                  f"joined in from a mesh without the {attr.name!r} color attribute?): exported white")
            for li in blank:
                cols[li] = (1.0, 1.0, 1.0, 1.0)
    me.uv_layers.new(name='N64_RG')
    me.uv_layers.new(name='N64_BA')
    uv_rg, uv_ba = me.uv_layers['N64_RG'], me.uv_layers['N64_BA']
    for li, c in enumerate(cols):
        uv_rg.data[li].uv = (c[0], c[1])
        uv_ba.data[li].uv = (c[2], c[3])

# meshes that won't be in the game: not in a collection the import reads
_known = re.compile(r'(Costume \d+|Layer \d+.*|Room .*|Hurtboxes|Reference|Shield.*|VS Screen|Collision.*|Stage objects.*)$')
for ob in bpy.data.objects:
    if ob.type != 'MESH' or ob.get('n64_reference') or bpy.context.scene.get('n64_kind') == 'stage':
        continue
    colls = [c.name for c in ob.users_collection]
    if any(c in EXPORTED_COLLECTIONS for c in colls):
        continue
    rigs = [md.object for md in ob.modifiers if md.type == 'ARMATURE' and md.object is not None] + \
        ([ob.parent] if ob.parent is not None and ob.parent.type == 'ARMATURE' else [])
    deforms = any(not r.get('n64_reference') for r in rigs)        # (not the reference opponent's)
    if deforms and not any(_known.match(c) for c in colls):
        print(f"  warning: {ob.name}: in {', '.join(colls) or 'no collection'}, not in "
              f"{' / '.join(EXPORTED_COLLECTIONS)}: not imported (move it into one of those)")
# the import goes by object name (without Blender's .001): two objects with one name are one piece
for coll_name in EXPORTED_COLLECTIONS:
    c = bpy.data.collections.get(coll_name)
    seen = {}
    for ob in (c.all_objects if c else []):
        if ob.type == 'MESH':
            seen.setdefault(re.sub(r'\.\d+$', '', ob.name), []).append(ob.name)
    for n, obs in seen.items():
        if len(obs) > 1:
            print(f"  note: {coll_name}: {', '.join(sorted(obs))} are all {n!r} for the import: drawn together")

arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE' and not o.get('n64_reference')), None)
LAYERS = []
for c in bpy.data.collections:             # stages: one glTF per type/layer, containing its rooms
    m = re.match(r'Layer (\d+)(?: - .*)?$', c.name)
    if m:
        LAYERS.append((c.name, f'layer{m.group(1)}.gltf'))
for coll_name, out in (('Costume 0', 'body.gltf'), ('Low detail', 'lo.gltf'), ('Model parts', 'parts.gltf'),
                       ('Electric skeleton', 'skeleton.gltf'), *LAYERS):
    lc = bpy.context.view_layer.layer_collection.children.get(coll_name)
    if lc is None:
        continue
    lc.exclude = False
    lc.hide_viewport = False
    bpy.context.view_layer.active_layer_collection = lc
    stage_layer = re.match(r'Layer \d+(?: - .*)?$', coll_name) is not None
    collection_objects = list(lc.collection.all_objects if stage_layer else lc.collection.objects)
    # the armature the collection's meshes deform with (stages: a room may be nested
    # under its type collection)
    arm = next((md.object for o in collection_objects if o.type == 'MESH' for md in o.modifiers
                if md.type == 'ARMATURE' and md.object), arm)
    linked = arm is not None and arm.name not in lc.collection.objects
    if linked:                       # the skin (bone per vertex) is only exported with its armature
        lc.collection.objects.link(arm)
        if stage_layer:
            collection_objects.append(arm)
    if stage_layer:
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        for ob in collection_objects:
            ob.select_set(True)
    opts = dict(filepath=os.path.join(OUT, out), export_format='GLTF_SEPARATE',
                use_active_collection=not stage_layer, export_image_format='NONE',
                export_animations=False, export_yup=True, export_materials='EXPORT',
                export_apply=True)        # modifiers (Mirror, Solidify...): what Blender shows
    if stage_layer:
        opts['use_selection'] = True
    try:      # vertex colors go as UV maps (above)
        bpy.ops.export_scene.gltf(**opts, export_vertex_color='NONE')
    except TypeError:
        bpy.ops.export_scene.gltf(**opts, export_colors=False)
    if linked:
        lc.collection.objects.unlink(arm)
    print('exported', coll_name, '->', out)
