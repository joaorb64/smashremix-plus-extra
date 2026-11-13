"""Export a fighter.blend (made by build_blend.py, then edited) for import_fighter.py.
Run inside Blender:

  blender -b fighter.blend --python blend_to_gltf.py -- OUT_DIR

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


def mat_info(m):
    if m is None:
        return None
    img, color = None, None
    if m.node_tree:
        for node in m.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image:
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
            info = mat_info(slot.material)
            if info and slot.material:
                costumes.setdefault(base_name(slot.material.name), {})[k] = info
# materials only used outside the costume collections (low detail, model parts):
# costume k's image is the costume-0 image's sibling <mat>_c<k>.png
for m in bpy.data.materials:
    name = base_name(m.name)
    if name in costumes or m.users == 0:
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
props = {}
for m in bpy.data.materials:
    name = base_name(m.name)
    if name in props and m.name != name:
        continue
    p = {k: (m[k] if isinstance(m[k], str) else str(m[k])) for k in ('n64_format', 'n64_wrap_s', 'n64_wrap_t',
                                                                      'n64_cull', 'n64_lighting', 'n64_light_color',
                                                                      'n64_shadow_color', 'n64_state') if k in m.keys()}
    # culling: Blender's Backface Culling checkbox (on: back faces culled, off: double-sided);
    # n64_cull only for what it can't say (front / both)
    if p.get('n64_cull') not in ('front', 'both'):
        p['n64_cull'] = 'back' if m.use_backface_culling else 'none'
    if p:
        props[name] = p
# images painted in Blender but not saved yet (expressions, palettes)
for img in bpy.data.images:
    if img.is_dirty and img.filepath:
        img.save()
export_dir = os.path.normpath(bpy.path.abspath(bpy.context.scene.get('fighter_export_dir', '//')))
sc = bpy.context.scene
lights = {k: str(sc[f'n64_{k}_color']) for k in ('light', 'shadow') if f'n64_{k}_color' in sc.keys()}
json.dump(dict(export_dir=export_dir, materials=costumes, props=props, lights=lights),
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
