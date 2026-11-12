"""Build an editable .blend from an export_fighter.py folder (run inside Blender).

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
Every image / palette list entry (facial expressions, extra palettes) is loaded
as an image (<mat>_img<i>, <mat>_pal<p>) to paint in the Image Editor.
Materials carry n64_format / n64_wrap_s / n64_wrap_t / n64_state properties.
Writes EXPORT_DIR/fighter.blend.
"""
import json
import os
import sys

import bpy

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
DIR = os.path.abspath(args[0])
man = json.load(open(os.path.join(DIR, 'manifest.json')))
N = man['costumes']

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
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
    return c


def move(ob, c):
    for old in list(ob.users_collection):
        old.objects.unlink(ob)
    c.objects.link(ob)


def image(rel):
    path = os.path.join(DIR, rel)
    img = bpy.data.images.load(path, check_existing=True)
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
    groups = {}
    for v in bm.verts:
        g = max(v[deform].items(), key=lambda kv: kv[1])[0] if v[deform] else -1
        groups.setdefault((g, vcol[v.index] if vcol else None), []).append(v)
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


arm = next((o for o in scene.objects if o.type == 'ARMATURE'), None)
c_arm = coll('Armature')
if arm:
    move(arm, c_arm)
    tidy_bones(arm)
meshes = [o for o in scene.objects if o.type == 'MESH']
hi = [o for o in meshes if o.name.startswith('hi_')]
lo = [o for o in meshes if o.name.startswith('lo_')]
parts = [o for o in meshes if o.name.startswith('part')]
skel = [o for o in meshes if o.name.startswith('skel')]

c_lo = coll('Low detail', hide=True)
for o in lo:
    move(o, c_lo)
c_parts = coll('Model parts', hide=True)
for o in parts:
    move(o, c_parts)
if skel:
    c_skel = coll('Electric skeleton', hide=True)
    for o in skel:
        move(o, c_skel)

# costume 0 = the imported hi objects; costume k = linked duplicates beside it
c0 = coll('Costume 0')
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
lights = man.get('lights') or {}
scene['n64_light_color'] = lights.get('light', 'FFFFFF')    # directional light color (hex RGB)
scene['n64_shadow_color'] = lights.get('shadow', '808080')  # ambient = shadow color (hex RGB)
scene['fighter_export_dir'] = '//'
scene['n64_collapse_outliner'] = True        # the panel script collapses the outliner on the first open          # where manifest.json + textures/ are (relative to the .blend)

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
height = max((max((ob.matrix_world @ v.co).z for v in ob.data.vertices) for ob in hi if ob.data.vertices), default=500)
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
            space.shading.type = 'MATERIAL'                    # Material Preview: shows the N64 setup
            space.shading.studiolight_intensity = 0.6          # (the preview light is much brighter than the game's)
            space.shading.studiolight_background_alpha = 0.0
            r3d = space.region_3d
            if r3d:
                r3d.view_location = (0, 0, height / 2)
                r3d.view_distance = height * 2.5
# the settings, visible in Blender too (wrap per axis, unlit effects, own light color)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import n64_fighter_panel as PANEL  # noqa: E402
if not os.environ.get('N64_NO_VIEW'):
    for m in bpy.data.materials:
        PANEL.update_view(m)

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

# the N64 Fighter panel (dropdowns / color pickers for the n64_* properties), registered
# when the .blend is opened with its scripts allowed (or install n64_fighter_panel.py as an add-on)
panel_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'n64_fighter_panel.py')
if os.path.exists(panel_src):
    txt = bpy.data.texts.get('n64_fighter_panel.py') or bpy.data.texts.new('n64_fighter_panel.py')
    txt.from_string(open(panel_src, encoding='utf-8').read())
    txt.use_module = True                          # 'Register': runs when the file is opened
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(DIR, 'fighter.blend'), relative_remap=True)
print(f'built fighter.blend: {len(hi)} body pieces x {N} costumes (shared meshes), '
      f'{len(lo)} low-detail, {len(parts)} part objects, {n_entries} image/palette entries')
