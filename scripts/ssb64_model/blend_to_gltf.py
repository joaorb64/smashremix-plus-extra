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
import blend_gamedata as GDB  # noqa: E402


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
# materials only used outside the costume collections (low detail, model parts):
# costume k's image is the costume-0 image's sibling <mat>_c<k>.png
for m in bpy.data.materials:
    name = base_name(m.name)
    if name in costumes or m.users == 0 or m.get('n64_helper'):
        continue
    info = mat_info(m)
    costumes[name] = {0: info}
    img = info.get('image') if info else None
    if img and re.search(r'_c0\.png$', img):
        for k in range(1, 32):
            sib = re.sub(r'_c0\.png$', f'_c{k}.png', img)
            if not os.path.exists(sib):
                break
            costumes[name][k] = dict(image=sib, color=info.get('color'))
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
    if p:
        props[name] = p
# images painted in Blender but not saved yet (expressions, palettes)
for img in bpy.data.images:
    if img.is_dirty and img.filepath:
        img.save()
export_dir = os.path.normpath(bpy.path.abspath(bpy.context.scene.get('n64_export_dir', bpy.context.scene.get('fighter_export_dir', '//'))))
sc = bpy.context.scene
lights = {k: str(sc[f'n64_{k}_color']) for k in ('light', 'shadow') if f'n64_{k}_color' in sc.keys()}


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
    for b in arm.data.bones:       # rotation only (bones have no rest scale: a joint's is in its pose)
        if b.name in by_name:
            j_rest = (A @ node_world(by_name[b.name]) @ Ai).to_3x3().normalized()
            corr[b.name] = (j_rest.inverted() @ b.matrix_local.to_3x3().normalized()).to_4x4()
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


# hurtboxes / stage collision (gamedata.py applies them)
json.dump(GDB.collect(), open(os.path.join(OUT, 'gamedata.json'), 'w'), indent=1)
json.dump(dict(export_dir=export_dir, materials=costumes, props=props, lights=lights, anims=anims,
               joint_anims=joint_anims()),
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

# vertex colors (unlit materials): the glTF exporter's color handling loses some (alpha 0,
# corner colors), so they travel as two extra UV maps (R,G) and (B,A): exact, per corner.
# Values are the game's (sRGB) bytes / 255. In memory only.
for me in {o.data for o in bpy.data.objects if o.type == 'MESH'}:      # (costumes share meshes)
    attr = me.color_attributes.active_color or (me.color_attributes[0] if me.color_attributes else None)
    if attr is None or 'N64_RG' in me.uv_layers:
        continue
    if len(me.uv_layers) > 6:
        print(f'  warning: {me.name}: no room for the vertex color UV maps (8 max); its colors use COLOR_0')
        continue
    uv_rg, uv_ba = me.uv_layers.new(name='N64_RG'), me.uv_layers.new(name='N64_BA')
    for li, loop in enumerate(me.loops):
        c = attr.data[li if attr.domain == 'CORNER' else loop.vertex_index].color_srgb
        uv_rg.data[li].uv = (c[0], c[1])
        uv_ba.data[li].uv = (c[2], c[3])

arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
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
                export_animations=False, export_yup=True, export_materials='EXPORT')
    if stage_layer:
        opts['use_selection'] = True
    try:      # vertex colors go as UV maps (above)
        bpy.ops.export_scene.gltf(**opts, export_vertex_color='NONE')
    except TypeError:
        bpy.ops.export_scene.gltf(**opts, export_colors=False)
    if linked:
        lc.collection.objects.unlink(arm)
    print('exported', coll_name, '->', out)
