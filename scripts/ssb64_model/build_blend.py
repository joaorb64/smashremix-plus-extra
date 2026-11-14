"""Build an editable .blend from an export_model.py folder (run inside Blender;
ssb64_model.py export runs it).

  blender -b --python build_blend.py -- EXPORT_DIR

Layout of the .blend:
  Costume 0 .. N-1   one collection per costume, all at the same place (only
                     Costume 0 shown: toggle the others' visibility). Every costume's
                     objects share the SAME meshes (linked duplicates): edit the
                     geometry once and it changes in every costume. Only the
                     materials differ (linked to the object, not the mesh), each
                     using that costume's textures (textures/<mat>_c<N>.png).
  Low detail         the lo-poly body (costume 0), hidden
  Model parts        alternate pieces (hands, held props...) per joint and variant
                     ('part<variant>_<hi|lo>_j<joint>'), hidden
  Electric skeleton  the shocked-skeleton model ('skel<id>_j<joint>'), hidden
  Armature           one skeleton (bones joint_00..), shared by everything
  Stages             a type/layer collection with a Room collection per DObj joint
Every image / palette list entry (facial expressions, extra palettes) is loaded
as an image (<mat>_img<i>, <mat>_pal<p>) to paint in the Image Editor.
Materials carry n64_format / n64_wrap_s / n64_wrap_t / n64_state properties.
Writes EXPORT_DIR/model.blend.
"""
import json
import re
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keyreduce as KR  # noqa: E402

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
DIR = os.path.abspath(args[0])
man = json.load(open(os.path.join(DIR, 'manifest.json')))
N = man['costumes']

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = 60          # the game's frame rate; before the glTF import, which turns the
                               # animations' seconds into frames at the scene's rate
try:     # bone tails toward the child joints (the default keeps each joint's own axes: bones
         # sticking out sideways, with sphere shapes). Display only: import_fighter.py takes
         # the joints from the game file and the vertices as they are, not the bones' rest.
    bpy.ops.import_scene.gltf(filepath=os.path.join(DIR, 'model.gltf'), bone_heuristic='TEMPERANCE')
except TypeError:
    bpy.ops.import_scene.gltf(filepath=os.path.join(DIR, 'model.gltf'))
imported = list(bpy.context.selected_objects) or list(scene.objects)


def coll(name, hide=False):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in scene.collection.children:
        scene.collection.children.link(c)
    if hide:
        bpy.context.view_layer.layer_collection.children[c.name].hide_viewport = True
        c.hide_render = True                      # (renders: what the viewport shows)
    return c


def child_coll(parent, name):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def move(ob, c):
    for old in list(ob.users_collection):
        old.objects.unlink(ob)
    c.objects.link(ob)


def image(rel):
    """The image datablock of an export PNG (one per file: nodes and painting share it)."""
    img = next((i for i in bpy.data.images if i.filepath == '//' + rel), None)
    if img is None:
        img = bpy.data.images.load(os.path.join(DIR, rel), check_existing=True)
        img.filepath = '//' + rel
    return img


def costume_material(base, k):
    """Copy of glTF material `base` using costume k's texture (flat colors stay)."""
    name = f'{base.name}_c{k}'
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m = base.copy()
    m.name = name
    entry = man['materials'].get(base.name, {})
    pngs = entry.get('costumes') or []
    if m.node_tree and k < len(pngs) and pngs[k]:
        for node in m.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = image(pngs[k])
    cc = entry.get('costume_colors') or []
    if entry.get('untextured') and m.node_tree and cc and 'prim' in cc[min(k, len(cc) - 1)]:
        set_base_color(m, cc[min(k, len(cc) - 1)]['prim'])   # this costume's color (edit it here)
    set_costume_lights(m, cc[min(k, len(cc) - 1)] if cc else {})
    return m


def set_costume_lights(m, colors):
    """An MObj's own light colors for this costume (vanilla Mario's shoes are white, lit
    by a brown light). Shown in Blender only (n64_costume_light / n64_costume_shadow):
    the importer keeps the MObj's colors from the manifest."""
    for key, track in (('n64_costume_light', 'light1'), ('n64_costume_shadow', 'light2')):
        if colors.get(track):
            m[key] = colors[track][:6]
        elif key in m:
            del m[key]


def set_base_color(m, hex_rgba):
    srgb = [int(hex_rgba[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    for node in m.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            node.inputs['Base Color'].default_value = (*lin, 1.0)
    m.diffuse_color = (*lin, 1.0)                 # solid view color


# pixel look + relative texture paths + N64 settings on the imported materials.
# Custom properties (Material > Custom Properties) that import_fighter.py reads:
#   n64_format   CI4 / CI8 / IA4 / IA8 / IA16 / I4 / I8 / RGBA16
#   n64_wrap_s / n64_wrap_t   repeat / mirror / clamp (per axis)
#   n64_cull     back / front / none / both (back-face culling; game default: back)
#   n64_lighting on / off (off: vertex colors instead of lighting, e.g. effects)
#   n64_light_color / n64_shadow_color  this material's own light colors (hex RGB),
#                instead of the model's (scene properties of the same names)
#   n64_state    the material's render state, "w0 w1; w0 w1; ..." (blend mode,
#                combiner, prim/env colors): copy one from another material to
#                get its look (e.g. an alpha-blended effect)
EXTENSION = {'repeat': 'REPEAT', 'mirror': 'MIRROR', 'clamp': 'EXTEND'}
for m in bpy.data.materials:
    entry = man['materials'].get(m.name)
    if entry and entry.get('kind') in ('mobj', 'tex', 'prim'):
        if not entry.get('untextured') and entry.get('kind') != 'prim':
            wrap = entry.get('wrap') or {}
            m['n64_format'] = entry.get('format', 'CI4')
            m['n64_wrap_s'] = wrap.get('s', 'clamp')
            m['n64_wrap_t'] = wrap.get('t', 'clamp')
            for a in 'st':          # tile clamped past the texture's size (vanilla fighters)
                if wrap.get(f'clamp_{a}'):
                    m[f'n64_clamp_{a}'] = float(wrap[f'clamp_{a}'])
        state = entry.get('state') or []
        cull = next((int(b, 16) for a, b in state if a.startswith('D9')), 0x400)
        m['n64_cull'] = {0: 'none', 0x400: 'back', 0x200: 'front', 0x600: 'both'}.get(cull & 0x600, 'back')
        m['n64_lighting'] = 'on' if cull & 0x20000 else 'off'
        m.use_backface_culling = m['n64_cull'] in ('back', 'both')       # viewport shows it too
        own = {a: b for a, b in state if a.startswith('DB0A')}           # its own light colors
        if 'DB0A0000' in own:
            m['n64_light_color'] = own['DB0A0000'][:6]
        if 'DB0A0018' in own:
            m['n64_shadow_color'] = own['DB0A0018'][:6]
        filt = next((b for a, b in state if a == 'E3001201'), None)
        if filt is not None:
            m['n64_filter'] = {0: 'point', 0x2000: 'bilinear', 0x3000: 'average'}.get(int(filt, 16), 'bilinear')
        m['n64_state'] = '; '.join(f'{a} {b}' for a, b in state if not a.startswith(('D9', 'DB', 'E3001201', 'E3001001')))
    if entry and entry.get('untextured') and m.node_tree and (entry.get('costume_colors') or [{}])[0].get('prim'):
        set_base_color(m, entry['costume_colors'][0]['prim'])
    if entry and entry.get('costume_colors'):
        set_costume_lights(m, entry['costume_colors'][0])
    elif entry and entry.get('kind') == 'prim' and m.node_tree and entry.get('color'):
        # one DL prim color, no MObj: the glTF import leaves Blender's default gray, and
        # import_fighter.py reads the base color back as the prim color
        set_base_color(m, ''.join(f'{v:02X}' for v in entry['color'][:3]))
    if m.node_tree:
        for node in m.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.interpolation = 'Linear'                # the game filters (bilinear)
                if entry and entry.get('wrap'):     # Blender has one mode for both axes: use S's
                    try:
                        node.extension = EXTENSION.get(entry['wrap'].get('s'), 'REPEAT')
                    except TypeError:               # older Blender: no MIRROR
                        node.extension = 'REPEAT'
                if node.image and node.image.filepath:
                    node.image.filepath = '//' + os.path.relpath(bpy.path.abspath(node.image.filepath), DIR)

# merge by distance, within each joint only (the glTF has every triangle corner as
# its own vertex); UVs and normals stay per face corner
import bmesh  # noqa: E402
# The model's own vertex normals (glTF -> custom normals) are kept through the merge:
# carried per face corner, then set again. (To let Blender compute them after editing:
# Mesh > Clean Up > Clear Custom Split Normals Data.)
for ob in [o for o in scene.objects if o.type == 'MESH']:
    me = ob.data
    # vertex colors (unlit materials): vertices with different colors are never merged
    vcol = None
    if me.color_attributes and me.color_attributes[0].domain == 'POINT':
        vcol = [tuple(round(x * 255) for x in d.color) for d in me.color_attributes[0].data]
    corner = [tuple(c.vector) for c in me.corner_normals]
    bm = bmesh.new()
    bm.from_mesh(me)
    lay = [bm.loops.layers.float.new(f'n{a}') for a in 'xyz']
    for face, poly in zip(bm.faces, me.polygons):
        for loop, li in zip(face.loops, poly.loop_indices):
            for k in range(3):
                loop[lay[k]] = corner[li][k]
    deform = bm.verts.layers.deform.verify()
    # faces lying exactly on another face (a decal / translucent layer drawn over it, e.g. a
    # window over its frame): merging their vertices would make them duplicates Blender
    # deletes, so each keeps its own vertices (every vertex is one face's corner here)
    sig_count, face_sig = {}, {}
    for face in bm.faces:
        sig = tuple(sorted(tuple(round(c, 1) for c in v.co) for v in face.verts))
        face_sig[face.index] = sig
        sig_count[sig] = sig_count.get(sig, 0) + 1
    own = {v.index: face.index for face in bm.faces if sig_count[face_sig[face.index]] > 1 for v in face.verts}
    groups = {}
    for v in bm.verts:
        g = max(v[deform].items(), key=lambda kv: kv[1])[0] if v[deform] else -1
        groups.setdefault((g, vcol[v.index] if vcol else None, own.get(v.index)), []).append(v)
    for vs in groups.values():
        bmesh.ops.remove_doubles(bm, verts=vs, dist=0.01)
    normals = [tuple(loop[lay[k]] for k in range(3)) for face in bm.faces for loop in face.loops]
    for l in lay:
        bm.loops.layers.float.remove(l)
    bm.to_mesh(me)
    bm.free()
    me.normals_split_custom_set(normals)
    me.update()

def tidy_bones(arm):
    """Readable bones (display only, see the glTF import above): each bone reaches to its
    children; a joint stacked on its child (N64 fighters chain joints at one spot for
    separate rotations: shoulders, neck, ankles) becomes a short stub along that child;
    end bones are half their parent's length. Colored by side: Left / Right / Center
    bone collections (the fighter's left is +X)."""
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones

    def depth(b):
        return 0 if b.parent is None else 1 + depth(b.parent)
    for b in sorted(eb, key=depth, reverse=True):     # children first: stubs follow them
        apart = [c for c in b.children if (c.head - b.head).length > 1.0]
        if len(apart) > 1:      # not toward a child at the origin (the ground point)
            apart = [c for c in apart if c.head.length > 1.0] or apart
        stacked = [c for c in b.children if (c.head - b.head).length <= 1.0]
        if apart:
            tgt = sum((c.head for c in apart), b.head * 0) / len(apart)
            if (tgt - b.head).length > 1.0:
                b.tail = tgt
        elif stacked:
            c = stacked[0]
            b.tail = b.head + (c.tail - c.head).normalized() * max(c.length * 0.35, 10.0)
        elif b.parent is not None:
            b.length = max(b.parent.length * 0.5, 15.0)
    bpy.ops.object.mode_set(mode='OBJECT')
    arm.show_in_front = True            # Viewport Display > Show: In Front, Names
    arm.data.show_names = True
    arm.data.display_type = 'STICK'     # Viewport Display > Display As
    try:
        groups = {}
        for name, theme in (('Center', 'THEME09'), ('Left', 'THEME01'), ('Right', 'THEME04')):
            groups[name] = arm.data.collections.get(name) or arm.data.collections.new(name)
            groups[name]['theme'] = theme
        for b in arm.data.bones:
            x = b.head_local.x
            side = 'Left' if x > 5 else 'Right' if x < -5 else 'Center'
            groups[side].assign(b)
            b.color.palette = groups[side]['theme']
    except AttributeError:      # Blender < 4: no bone collections / colors
        pass


IS_STAGE = (man.get('object') or {}).get('kind') == 'stage'
arm = next((o for o in scene.objects if o.type == 'ARMATURE'), None)
c_arm = coll('Armature')
for a in [o for o in scene.objects if o.type == 'ARMATURE']:     # (stages: the glTF import merges
    move(a, c_arm)                                                 # the layers' skins into one)
    rest_before = {b.name: b.matrix_local.to_3x3() for b in a.data.bones}
    tidy_bones(a)
    # the imported actions' keys are in the bones' axes from before: re-expressed in the new ones
    # (the same poses), or e.g. TransN's forward root motion would play downward
    bone_names = set(rest_before)
    acts_ = [act for act in bpy.data.actions
             if any(KR._bone_of(fc)[0] in bone_names for fc in KR.action_fcurves(act))]
    n_ = KR.rebase_actions(a, rest_before, acts_)
    if n_:
        print(f'{a.name}: {n_} animation curves re-expressed in the tidied bones\' axes')
if IS_STAGE:
    for a in [o for o in scene.objects if o.type == 'ARMATURE']:
        a.name = 'Stage'                   # every layer's bones (L<n>_joint_NN), and the animation
if man.get('animations'):                  # joint animations (stage layers: L<n>_joint_NN; items,
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))          # projectiles: joint_NN)
    import n64_panel as _P  # noqa: E402

    def _bone(L, j):
        return f'{L}_joint_{int(j):02d}' if L != 'hi' else f'joint_{int(j):02d}'
    for a in [o for o in scene.objects if o.type == 'ARMATURE']:
        # each animated joint's own length and loop (Bone > N64 Joint Animation; the import
        # writes its script from them)
        for L, an in man['animations'].items():
            for j, ji in (an.get('joint_info') or {}).items():
                pb = a.pose.bones.get(_bone(L, j))
                if pb is None:
                    continue
                pb['n64_anim_frames'] = int(ji['frames'])
                pb['n64_anim_loop'] = bool(ji['loop'])
                pb['n64_anim_loop_start'] = int(ji.get('loop_start', 0))
                if ji.get('locked'):
                    pb['n64_anim_locked'] = True        # (a translation path: kept as it is)
        for fc in (_P.anim_fcurves(a) or []):             # keys on whole frames (the glTF's times
            for kp in fc.keyframe_points:                  # come back as 499.99997...)
                d_ = round(kp.co.x) - kp.co.x
                kp.co.x += d_
                kp.handle_left.x += d_
                kp.handle_right.x += d_
            fc.update()
        # keys only where needed (the glTF has one per frame), within tolerance of the game
        # (the game's key frames first; more only where Blender's curve can't follow the game's
        # within tolerance: the import samples every frame, so an unedited stage keeps its scripts)
        hints_ = {_bone(L, j): ji.get('keys') or () for L, an in man['animations'].items()
                  for j, ji in (an.get('joint_info') or {}).items()}
        b_, a_, e_ = KR.reduce_fcurves(_P.anim_fcurves(a) or [], hints=hints_)
        if b_:
            print(f"joint animation keys: {b_} -> {a_} (worst frame: {e_['rotation']:.4f} deg, "
                  f"{e_['location']:.4f} units, {e_['scale']:.5f} scale)")
        _P.sync_loops()                                    # looping joints repeat (Cycles), each at its own length
FIGHTER_ANIMS = man.get('fighter_animations') or {}
if FIGHTER_ANIMS and arm is not None:
    # Fighter animations (figatree.py): one action each, named after its file (ROM: its first
    # action), kept and not assigned (pick one in the Action Editor). Keys: as few as follow the
    # game's motion (keyreduce.TOL_ANIM: ~0.5 degrees, 0.25 units), on the game's own key frames
    # where they can be (baked animations, a key every frame, keep the ones that matter); each
    # action's frame range is the animation's.
    n_b = n_a = 0
    worst = 0.0
    for name, info in FIGHTER_ANIMS.items():
        act = bpy.data.actions.get(info.get('gltf', name))
        if act is None:
            print(f'  note: animation {name!r} not imported')
            continue
        act.name = name                                   # its file | every action playing it
        act.use_fake_user = True
        b_, a_, e_ = KR.reduce_fcurves(KR.action_fcurves(act), hints=info.get('keys') or {}, candidates=True,
                                       tol=KR.TOL_ANIM)
        n_b, n_a, worst = n_b + b_, n_a + a_, max(worst, e_['rotation'])
        try:
            act.use_frame_range = True
            act.frame_start, act.frame_end = 0, max(1, int(info['frames']) - 1)
        except (AttributeError, TypeError):
            pass
        act['n64_flags'] = info['flags']                  # the motion flags (hidden parts, ...):
        act['n64_actions'] = ', '.join(info.get('actions') or []) or '(none in main.asm)'   # see the
        act['n64_flags_guessed'] = bool(info.get('flags_guessed'))         # N64 Animation panel
        act['n64_source'] = info.get('source') or ''
        act['n64_loop'] = bool(info.get('loop'))          # its scripts start over at the end
        # what it was exported as (blend_to_gltf.py writes back the actions that changed)
        act['n64_export'] = dict(name=name, flags=info['flags'], loop=bool(info.get('loop')))
    arm['n64_hidden_parts'] = list(man.get('hidden_parts') or [])
    arm['n64_hidden_part_parents'] = list(man.get('hidden_part_parents') or [])
    print(f"fighter animations: {len(FIGHTER_ANIMS)}, keys {n_b} -> {n_a} (Blender's curves are at "
          f"most {worst:.2f} deg from the game's)")
    for name in FIGHTER_ANIMS:                            # (after every curve is final)
        act = bpy.data.actions.get(name)
        if act is not None and 'n64_export' in act.keys():
            act['n64_export']['keys'] = KR.action_signature(act)
    ad = arm.animation_data
    if ad is not None:
        for tr in list(ad.nla_tracks):
            ad.nla_tracks.remove(tr)
        ad.action = None
    for pb in arm.pose.bones:
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
SHIELD = man.get('shield')
if SHIELD and arm is not None:
    # The shield pose (shieldpose.py): actions 'Shield' (frame = stick angle in degrees: 0
    # forward, 90 up, 180 back, 270 down) and 'Shield Neutral' (the pose every scripted joint
    # leans toward as the stick comes back to the center). Kept, not assigned: the armature
    # stays at rest; pick one in the Action Editor to see it. The bubble (radius shield_size,
    # full health) follows YRotN, where the shield sits.
    for act in bpy.data.actions:
        if act.name in ('Shield', 'Shield Neutral'):
            act.use_fake_user = True
            # keys exactly on the game's key frames (the sector starts, every 45, and where the
            # scripts set values), with the game's values; Bezier handles fitted between
            try:
                act.use_frame_range = True
                act.frame_start, act.frame_end = 0, int(SHIELD['frames']) - 1
            except (AttributeError, TypeError):
                pass
            hints_ = SHIELD.get('keys') or range(0, int(SHIELD['frames']), 45)
            b_, a_, e_ = KR.reduce_fcurves(KR.action_fcurves(act), hints=hints_, exact=True)
            print(f"{act.name}: keys {b_} -> {a_} (between keys, Blender's curve is at most "
                  f"{e_['rotation']:.2f} deg / {e_['location']:.2f} units from the game's)")
            # per bone, as built: the import tells the bones edited since (blend_to_gltf.py)
            act['n64_bone_signatures'] = KR.bone_signatures(act)
    ad = arm.animation_data
    if ad is not None:
        for tr in list(ad.nla_tracks):
            ad.nla_tracks.remove(tr)
        ad.action = None
    for pb in arm.pose.bones:
        pb.location, pb.rotation_quaternion, pb.scale = (0, 0, 0), (1, 0, 0, 0), (1, 1, 1)
    if 'YRotN' in arm.data.bones:
        c_sh = coll('Shield', hide=True)
        bub = bpy.data.objects.new('Shield Bubble', None)
        bub.empty_display_type = 'SPHERE'
        bub.empty_display_size = float(SHIELD.get('radius') or 30.0)
        c_sh.objects.link(bub)
        bub.parent = arm
        bub.parent_type = 'BONE'
        bub.parent_bone = 'YRotN'
        bpy.context.view_layer.update()
        bub.matrix_world = arm.matrix_world @ arm.data.bones['YRotN'].matrix_local     # at the joint
        bub.show_in_front = True
    scene['n64_shield_frames'] = int(SHIELD['frames'])
if IS_STAGE:
    # One type collection per MPGroundData slot (layer), holding its rooms' meshes.
    stage_types = {
        0: 'Background',
        1: 'Stage (Z-buffered)',
        2: 'Foreground',
        3: 'Foreground',
    }
    for o in sorted([o for o in scene.objects if o.type == 'MESH'], key=lambda o: o.name):
        m = re.match(r'L(\d+)_j(\d+)', o.name)
        if not m:
            continue
        layer, joint = map(int, m.groups())
        layer_coll = coll(f'Layer {layer} - {stage_types.get(layer, "Other")}')
        layer_coll['stage_layer'] = layer
        layer_coll['stage_type'] = stage_types.get(layer, 'Other')
        # one mesh per room (DObj joint): straight in its layer; the room is in its name
        # (L<layer>_j<joint>) and custom properties
        o['stage_room'] = joint
        o['stage_layer'] = layer
        move(o, layer_coll)
        # Layer 0 is painted first in the game, with no depth test: whatever comes after it
        # covers it, even where it's closer (Gym Leader's horn feet under the floor); layers 2 / 3
        # cover everything: renders composite the layers in that order (render passes below).
        o.show_in_front = False                  # (see n64_panel LAYER_DEPTH)
meshes = [o for o in scene.objects if o.type == 'MESH']
hi = [o for o in meshes if o.name.startswith('hi_')]
lo = [o for o in meshes if o.name.startswith('lo_')]
parts = [o for o in meshes if o.name.startswith('part')]
skel = [o for o in meshes if o.name.startswith('skel')]

if lo or not IS_STAGE:
    c_lo = coll('Low detail', hide=True)
    for o in lo:
        move(o, c_lo)
if parts or not IS_STAGE:
    c_parts = coll('Model parts', hide=True)
    for o in parts:
        move(o, c_parts)
if skel:
    c_skel = coll('Electric skeleton', hide=True)
    for o in skel:
        move(o, c_skel)

# costume 0 = the imported hi objects; costume k = linked duplicates beside it
c0 = coll('Costume 0') if not IS_STAGE else None
for o in hi:
    move(o, c0)
for k in range(1, N):
    ck = coll(f'Costume {k}', hide=True)
    for o in hi:
        d = o.copy()                       # shares o.data (the mesh): edits show in every costume
        d.name = f'{o.name}_c{k}'
        ck.objects.link(d)
        # (at the same place as costume 0; only costume 0 is shown: toggle the collections)
        for slot in d.material_slots:
            base = slot.material
            slot.link = 'OBJECT'
            slot.material = costume_material(base, k)

# every image / palette list entry (expressions, extra palettes) as an image to
# open and paint in the Image Editor (saved in place; the importer reads them)
n_entries = 0
for name, entry in man['materials'].items():
    for rel in (entry.get('images') or []) + (entry.get('palettes') or []):
        if rel and os.path.exists(os.path.join(DIR, rel)):
            image(rel).use_fake_user = True
            n_entries += 1
# ---- material animations (stages, items): matanim.py tracks as animatable custom properties
# (n64_offset_x, n64_tex_index, n64_prim_color...) keyed like the game plays them; the image /
# palette list entries as 'N64 Frame <i>' / 'N64 Palette <i>' texture nodes the preview flips
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matanim as MA  # noqa: E402
import n64_panel as PANEL  # noqa: E402
anim_frames = 0
IS_OBJECT = bool(man.get('object'))
for m in list(bpy.data.materials):
    entry = man['materials'].get(m.name)
    if not entry or not IS_OBJECT or m.node_tree is None:
        continue
    if entry.get('tile'):            # the MObj's tile setup (drawn offsets; not edited)
        t = entry['tile']
        m['n64_mobj'] = dict(flags=int(t['flags']), mode=int(t['mode']), unk0a=int(t['unk0a']),
                             size0=[int(x) for x in t['size0']], size1=[int(x) for x in t['size1']],
                             tex_size=[int(x) for x in (entry.get('size') or t['size0'])])
    anim = entry.get('anim') or {}
    keys = anim.get('keys') or {}
    static = entry.get('mobj_static') or {}
    flags = (entry.get('tile') or {}).get('flags', entry.get('flags', 0))
    for track in MA.TRACKS:          # the values the MObj starts with (offsets / scales it draws with)
        if track in keys or (track in static and flags & MA.FLAG_OF[track] and track not in MA.DISCRETE
                             and track != 'lfrac'):
            PANEL.anim_prop_create(m, MA.PROP[track], static.get(track, 0.0))
    for track in MA.COLORS:
        if track in keys:
            PANEL.anim_prop_create(m, MA.PROP[track], keys[track][0][1])
    if keys:
        m['n64_anim_frames'] = int(anim['frames'])
        m['n64_anim_loop'] = bool(anim['loop'])
        m['n64_anim_loop_start'] = int(anim.get('loop_start', 0))
        anim_frames = max(anim_frames, int(anim['frames']))
        for track, ks in keys.items():
            PANEL.anim_set_keys(m, MA.PROP[track], ks, color=track in MA.COLORS,
                                cycle=bool(anim['loop']) and not anim.get('loop_start'))
    # image / palette list entries the animation flips through (texture index / palette index)
    tex = PANEL._tex_node(m)
    for kind, rels in (('Frame', entry.get('images') or []), ('Palette', entry.get('palettes') or [])):
        if len(rels) < 2 or tex is None:
            continue
        for i, rel in enumerate(rels):
            if rel and os.path.exists(os.path.join(DIR, rel)):
                n = PANEL.frame_node(m, kind, i)
                n.image = image(rel)
lights = man.get('lights') or {}
scene['n64_light_color'] = lights.get('light', 'FFFFFF')    # directional light color (hex RGB)
scene['n64_shadow_color'] = lights.get('shadow', '808080')
anims = man.get('animations') or {}
if anims or anim_frames:                      # stages: their joint animations (glTF 'Stage'), and
    scene.render.fps = 60                     # material animations; 60 fps like the game
    scene.frame_start = 0
    scene.frame_end = max([a['frames'] for a in anims.values()] + [anim_frames]) - 1
    scene.frame_set(0)
if IS_OBJECT:
    scene['n64_kind'] = (man.get('object') or {}).get('kind') or 'item'

# the scene light's direction (stages: their light_angle; fighters / items: the usual one)
view = man.get('stage_view') or {}
scene['n64_light_angle'] = [float(x) for x in (view.get('light_angle') or PANEL.DEFAULT_LIGHT_ANGLE)[:2]]
try:
    scene.id_properties_ui('n64_light_angle').update(
        description='The game\'s light direction (degrees, x / y: the stage\'s light_angle); every lit '
                    'material is shaded by it with its light / shadow colors', step=100, precision=1)
except (TypeError, AttributeError):
    pass
if man.get('object'):                         # items / projectiles: see n64_panel build_combiner
    scene['n64_default_env'] = '00000000'  # ambient = shadow color (hex RGB)
scene['n64_export_dir'] = '//'
scene['n64_collapse_outliner'] = True        # the panel script collapses the outliner on the first open          # where manifest.json + textures/ are (relative to the .blend)

# ---- the game's camera (gmcamera.c): 38 degrees vertical field of view, 320 x 240, tilted down
# by the stage's light_angle z (radians), framing the stage's camera bounds (else the model)
import math  # noqa: E402
import mathutils  # noqa: E402
cam_data = bpy.data.cameras.new('Game Camera')
cam_data.sensor_fit = 'VERTICAL'
cam_data.angle_y = math.radians(38.0)
cam_data.clip_start, cam_data.clip_end = 128.0, 100000.0
cam = bpy.data.objects.new('Game Camera', cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 320, 240, 300
tilt = -float((view.get('light_angle') or [0, 0, -0.17453294])[2])
c0_coll = bpy.data.collections.get('Costume 0')
meshes_all = list(c0_coll.objects) if c0_coll and not IS_STAGE else \
    [o for o in scene.objects if o.type == 'MESH']
meshes_all = [o for o in meshes_all if o.type == 'MESH']
side = False                              # a flat model facing +-x (a projectile sprite: the game
if not view.get('camera_bounds'):         # turns it to the camera): looked at from its side
    pts = [o.matrix_world @ v.co for o in meshes_all for v in o.data.vertices]
    if pts:
        ext = [max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3)]
        side = ext[0] < 0.05 * max(ext[1], ext[2])
if view.get('camera_bounds'):
    top, bottom, right, left = view['camera_bounds']
    cx, cy, half_w, half_h = (left + right) / 2, (top + bottom) / 2, (right - left) / 2, (top - bottom) / 2
else:
    pts = [o.matrix_world @ v.co for o in meshes_all for v in o.data.vertices] or [mathutils.Vector((0, 0, 0))]
    xs, zs = [(p.y if side else p.x) for p in pts], [p.z for p in pts]
    cx, cy, half_w, half_h = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2, \
        max(1.0, (max(xs) - min(xs)) / 2 * 1.15), max(1.0, (max(zs) - min(zs)) / 2 * 1.15)
t = math.tan(math.radians(19.0))
dist = max(half_h / t, half_w / (t * 320 / 240))
# game (x, y, z) is Blender (x, -z, y): the camera looks along the game's -z, from +z
cam.location = (cx, -dist * math.cos(tilt), cy + dist * math.sin(tilt))
cam.rotation_euler = (math.radians(90.0) - tilt, 0.0, 0.0)
if side:
    cam.location = (dist * math.cos(tilt), cx, cy + dist * math.sin(tilt))
    cam.rotation_euler = (math.radians(90.0) - tilt, 0.0, math.radians(90.0))

# ---- stage renders (F12): one view layer per stage layer, composited in the game's order
# (grdisplay.c: layer 0 painted first without depth, then the z-buffered layer 1, then 2 and 3
# over everything). Each layer depth-tests only itself; the later one is laid over the earlier.
# The viewport can't composite view layers: it shows every layer depth-tested together.
layer_colls = sorted(((c['stage_layer'], c) for c in bpy.data.collections if 'stage_layer' in c.keys()),
                     key=lambda x: x[0])
if IS_STAGE and len(layer_colls) > 1:
    base = scene.view_layers[0]
    base.use = False                          # (the editing view: every layer, not rendered)
    scene.render.film_transparent = True
    vls = []
    for L, c in layer_colls:
        vl = scene.view_layers.get(f'Stage Layer {L}') or scene.view_layers.new(f'Stage Layer {L}')
        for other_L, other in layer_colls:
            lc = vl.layer_collection.children.get(other.name)
            if lc is not None:
                lc.exclude = other_L != L
        for name in ('Armature', 'Low detail', 'Model parts', 'Electric skeleton'):
            lc = vl.layer_collection.children.get(name)
            if lc is not None:
                lc.exclude = True
        vls.append(vl)
    if hasattr(scene, 'compositing_node_group'):          # Blender 5: a compositor node group
        tree = bpy.data.node_groups.new('N64 Stage Layers', 'CompositorNodeTree')
        tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        scene.compositing_node_group = tree
        out = tree.nodes.new('NodeGroupOutput')
        out_socket = out.inputs[0]
    else:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()
        out = tree.nodes.new('CompositorNodeComposite')
        out_socket = out.inputs['Image']
    prev = None
    for k, vl in enumerate(vls):
        rl = tree.nodes.new('CompositorNodeRLayers')
        rl.layer = vl.name
        rl.location = (0, -300 * k)
        if prev is None:
            prev = rl.outputs['Image']
            continue
        over = tree.nodes.new('CompositorNodeAlphaOver')          # the later layer over the earlier
        over.location = (300, -300 * k)
        tree.links.new(prev, over.inputs['Background' if 'Background' in over.inputs else 1])
        tree.links.new(rl.outputs['Image'], over.inputs['Foreground' if 'Foreground' in over.inputs else 2])
        prev = over.outputs['Image']
    out.location = (600, 0)
    tree.links.new(prev, out_socket)

# colors as stored (no filmic / AgX look)
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'

# no specular shine (the game has none), texture colors in the solid view, and a far
# clipping distance that fits a fighter (they're hundreds of units tall)
for m in bpy.data.materials:
    if m.node_tree:
        for node in m.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                for inp in ('Specular IOR Level', 'Specular'):
                    if inp in node.inputs:
                        node.inputs[inp].default_value = 0.0
                node.inputs['Roughness'].default_value = 1.0
    m.specular_intensity = 0.0
    m.roughness = 1.0
view_objs = hi or [o for o in scene.objects if o.type == 'MESH']          # stages: every layer
height = max((max((ob.matrix_world @ v.co).z for v in ob.data.vertices) for ob in view_objs if ob.data.vertices),
             default=500)
if IS_STAGE:                                  # a whole stage: frame its width too
    xs = [(ob.matrix_world @ v.co).x for ob in view_objs for v in ob.data.vertices]
    height = max(height, (max(xs) - min(xs)) / 2 if xs else 0)
# the viewport renders with EEVEE: a plain gray world (the viewport's usual background; the
# emission materials don't take light from it), nothing it doesn't need
for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):      # (4.2 - 4.x / 5.x)
    try:
        scene.render.engine = engine
        break
    except TypeError:
        pass
if scene.world is None:
    scene.world = bpy.data.worlds.new('N64 Background')
scene.world.color = (0.045, 0.045, 0.045)
try:
    scene.world.use_nodes = False
except (AttributeError, TypeError):
    pass
bg = next((n for n in (scene.world.node_tree.nodes if scene.world.node_tree else []) if n.type == 'BACKGROUND'), None)
if bg is not None:                            # (Blender 5: worlds always use their nodes)
    bg.inputs['Color'].default_value = (0.045, 0.045, 0.045, 1.0)
    bg.inputs['Strength'].default_value = 1.0
for attr, value in (('use_shadows', False), ('use_raytracing', False), ('use_gtao', False),
                    ('use_bloom', False), ('use_ssr', False), ('use_volumetric_shadows', False)):
    if hasattr(scene.eevee, attr):
        try:
            setattr(scene.eevee, attr, value)
        except (AttributeError, TypeError):
            pass
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for space in area.spaces:
            if space.type != 'VIEW_3D':
                continue
            space.clip_start = 1.0
            space.clip_end = 100000.0
            space.shading.color_type = 'TEXTURE'               # (Solid view, if switched to)
            space.shading.show_specular_highlight = False
            # Material Preview with the scene's plain world and lights (none): shows the N64 setup
            # (the materials are emission: the game's lighting is in the shader, no Blender light
            # or HDRI changes them), without lighting the scene from the studio HDRI, which is
            # what made it slow
            space.shading.type = 'MATERIAL'
            space.shading.use_scene_world = True
            space.shading.use_scene_lights = True
            space.shading.studiolight_intensity = 0.6
            space.shading.studiolight_background_alpha = 0.0
            # no viewport compositor: the stage-layer compositing tree combines the per-layer view
            # layers, which the viewport doesn't render, so it would show an empty view
            if hasattr(space.shading, 'use_compositor'):
                space.shading.use_compositor = 'DISABLED'
            space.overlay.show_stats = True                    # (Overlays > Statistics: triangle counts)
            r3d = space.region_3d
            if r3d:
                r3d.view_location = (0, 0, height / 2)
                r3d.view_distance = height * 2.5
# the settings, visible in Blender too (wrap per axis, unlit effects, own light color, animation)
if not os.environ.get('N64_NO_VIEW'):
    for m in bpy.data.materials:
        PANEL.update_view(m)

# hurtboxes (fighters), collision lines / map objects / spawns / bounds (stages): objects to edit
import blend_gamedata as GDB  # noqa: E402
GDB.build(man)
for ob in bpy.data.objects:
    if ob.get('n64_gamedata') == 'collision_group':
        PANEL._set_collision_group_renderer(ob)

# tooltips on the raw custom properties (also without the panel)
PROP_DESC = {
    'n64_format': 'Texture format: CI4, CI8, IA4, IA8, IA16, I4, I8, RGBA16',
    'n64_wrap_s': 'Past the texture\'s left / right edge: clamp, repeat or mirror',
    'n64_wrap_t': 'Past the texture\'s top / bottom edge: clamp, repeat or mirror',
    'n64_cull': 'Faces not drawn: back (default), none (double-sided), front, both',
    'n64_lighting': 'on: lit by the light / shadow colors; off: shaded by the vertex colors (effects)',
    'n64_light_color': 'This material\'s own light color (hex RGB), instead of the model\'s',
    'n64_shadow_color': 'This material\'s own shadow (ambient) color (hex RGB), instead of the model\'s',
    'n64_state': 'Render state, GBI commands "w0 w1; ...": blend mode (E2), combiner (FC), prim / env colors '
                 '(FA / FB). Copy from another material to get its look',
}
for idb in list(bpy.data.materials) + [scene]:
    for k, desc in PROP_DESC.items():
        if k in idb.keys():
            try:
                idb.id_properties_ui(k).update(description=desc)
            except (TypeError, AttributeError):
                pass

# fighters: the 1P mode VS screen's cameras, in a collection that's off until enabled
# (n64_panel make_vs_screen / _sync_vs_screen)
if man.get('vs_1p') and bpy.data.collections.get('Costume 0') is not None:
    PANEL.make_vs_screen(scene, man['vs_1p'])

# the N64 Model panel (dropdowns / color pickers for the n64_* properties), registered
# when the .blend is opened with its scripts allowed (or install n64_panel.py as an add-on)
panel_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'n64_panel.py')
if os.path.exists(panel_src):
    txt = bpy.data.texts.get('n64_panel.py') or bpy.data.texts.new('n64_panel.py')
    txt.from_string(open(panel_src, encoding='utf-8').read())
    txt.use_module = True                          # 'Register': runs when the file is opened

# the Animation workspace's Dope Sheet opens as the Action Editor (the armature's actions,
# e.g. the shield pose's, one dropdown away), with the armature active
for ws in bpy.data.workspaces:
    if ws.name != 'Animation':
        continue
    for scr in ws.screens:
        for area in scr.areas:
            if area.type == 'DOPESHEET_EDITOR':
                for sp in area.spaces:
                    if sp.type == 'DOPESHEET_EDITOR':
                        sp.ui_mode = 'ACTION'
                        sp.show_region_ui = True       # its sidebar: N64 Animation, first in Action
if arm is not None and arm.name in bpy.data.objects:
    bpy.context.view_layer.objects.active = arm
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(DIR, 'model.blend'), relative_remap=True)
print(f'built model.blend: {len(hi)} body pieces x {N} costumes (shared meshes), '
      f'{len(lo)} low-detail, {len(parts)} part objects, {n_entries} image/palette entries')
