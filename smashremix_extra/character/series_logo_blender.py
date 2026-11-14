"""Runs inside Blender for series_logo.py:

    blender -b LOGO.blend --python series_logo_blender.py -- dump OUT.json
        every visible mesh object's triangles (world space, modifiers applied,
        corner normals) -> OUT.json
    blender -b TEMPLATE.blend --python series_logo_blender.py -- make MESH.json OUT.blend
        replaces the template's logo mesh with MESH.json's triangles
"""
import json
import sys

import bpy

README = """Series logo for SR Extra

Edit the logo mesh, keep it facing the front view (-Y), about 3 units across
(like the Mario logo this started from). Anything visible in the render counts:
several objects, modifiers, any materials (they're ignored).

The build makes from it:
- the 3D logo of the results / data screens, with the player-color material
- series_logo.png (CSS) and hud.png (HUD emblem), unless the character folder
  has hand-made ones

The CSS and HUD cameras only preview those images: the build fits the logo
itself. Preview the build's output with
  python3 appender/scripts/series_logo.py render extra_characters/<Name> OUT_DIR
"""


def visible_meshes():
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    for ob in scene.objects:
        if ob.type != 'MESH' or ob.hide_render:
            continue
        if not ob.visible_get(view_layer=view_layer):
            continue
        yield ob


def dump(out):
    dg = bpy.context.evaluated_depsgraph_get()
    tris = []
    for ob in visible_meshes():
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            mw = ob.matrix_world
            nm = mw.to_3x3().inverted_safe().transposed()
            me.calc_loop_triangles()
            normals = me.corner_normals
            for lt in me.loop_triangles:
                tri = []
                for li in lt.loops:
                    vi = me.loops[li].vertex_index
                    p = mw @ me.vertices[vi].co
                    n = (nm @ normals[li].vector).normalized()
                    tri.append([[p.x, p.y, p.z], [n.x, n.y, n.z]])
                tris.append(tri)
        finally:
            ev.to_mesh_clear()
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"triangles": tris}, f)
    print(f"series_logo: {len(tris)} triangles")


def make(mesh_json, out):
    with open(mesh_json, "r", encoding="utf-8") as f:
        tris = json.load(f)["triangles"]
    meshes = [ob for ob in bpy.context.scene.objects if ob.type == 'MESH']
    target = next((ob for ob in meshes if ob.name == "Room01"),
                  meshes[0] if meshes else None)
    if target is None:
        raise SystemExit("template has no mesh object")

    # merge identical positions so the mesh edits as one piece
    verts, index, faces, normals = [], {}, [], []
    for tri in tris:
        face = []
        for pos, nrm in tri:
            key = tuple(round(c, 5) for c in pos)
            if key not in index:
                index[key] = len(verts)
                verts.append(pos)
            face.append(index[key])
            normals.append(nrm)
        faces.append(face)

    # the mesh lives in the object's local space: undo its world matrix
    inv = target.matrix_world.inverted()
    nm = target.matrix_world.to_3x3().transposed()
    from mathutils import Vector
    local = [inv @ Vector(p) for p in verts]
    me = bpy.data.meshes.new("SeriesLogo")
    me.from_pydata([tuple(v) for v in local], [], faces)
    me.validate()
    me.update()
    loop_normals = [tuple((nm @ Vector(n)).normalized()) for n in normals]
    if len(loop_normals) == len(me.loops):
        me.normals_split_custom_set(loop_normals)
    for mat in target.data.materials:
        me.materials.append(mat)
    old = target.data
    target.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    me.name = "SeriesLogo"
    readme = bpy.data.texts.get("README") or bpy.data.texts.new("README")
    readme.from_string(README)
    bpy.ops.wm.save_as_mainfile(filepath=out, check_existing=False,
                                compress=True)
    print(f"series_logo: wrote {out} ({len(faces)} triangles)")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    if argv[0] == "dump":
        dump(argv[1])
    elif argv[0] == "make":
        make(argv[1], argv[2])
    else:
        raise SystemExit(f"unknown command {argv[0]}")


main()
