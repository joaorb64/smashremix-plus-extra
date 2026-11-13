"""Blender side of gamedata.py: fighter hurtboxes and stage collision as objects to edit.

build(man): from the export manifest, in build_blend.py
  Hurtboxes            (hidden) cubes 'hurtbox <joint> <placement>', parented to their joint's bone; the
                       object's location / scale (along the joint's axes) are the box. Colors:
                       low blue, mid yellow, high red. Object > N64 Game Data / Bone > N64
                       Hurtboxes: placement, grabbable, offset, size (the import orders them)
  Collision > Group N  one edge mesh per collision group; each edge carries its collision type and flags
                       (the yakumono id hazards toggle). Lines lie on the game's z = 0 plane
                       (Blender's front view, y = 0)
  Stage objects        empties: map objects (spawn points, item spawns, rebirth platform...)
                       and spawn locations; rectangles: blast zones, camera bounds
collect(): what the .blend has, for gamedata.json (blend_to_gltf.py).
"""
import re

import bpy
import mathutils

GAME_TO_BLENDER = mathutils.Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
PLACEMENTS = ('low', 'mid', 'high')
LINE_TYPES = ('floor', 'ceil', 'rwall', 'lwall')
FLAG_LEDGE, FLAG_DROP = 0x8000, 0x4000
PLACEMENT_COLOR = {'low': (0.2, 0.45, 1.0, 0.3), 'mid': (1.0, 0.85, 0.1, 0.3), 'high': (1.0, 0.2, 0.2, 0.3)}
LINE_COLOR = {'floor': (0.1, 0.85, 0.2, 1), 'ceil': (0.9, 0.15, 0.15, 1),
              'rwall': (0.15, 0.4, 1.0, 1), 'lwall': (1.0, 0.55, 0.1, 1)}
COLL_EDGE_TYPE = 'n64_collision_type'
COLL_EDGE_FLAGS = 'n64_collision_flags'
COLL_EDGE_ORDER = 'n64_collision_line_order'
COLL_EDGE_SEGMENT = 'n64_collision_segment'
COLL_EDGE_START_SIDE = 'n64_collision_start_side'
COLL_EDGE_END_FLAGS = 'n64_collision_end_flags'
COLL_EDGE_POINT_FLAGS = 'n64_collision_has_point_flags'
MAPOBJ_KIND = {
    0x00: "P1 start", 0x01: "P2 start", 0x02: "P3 start", 0x03: "P4 start",
    0x04: "item spawn", 0x05: "scale L", 0x06: "scale R", 0x07: "pakkun L",
    0x08: "pakkun R", 0x09: "power block", 0x0A: "pipe L", 0x0B: "pipe R",
    0x0C: "acid", 0x0D: "twister", 0x0E: "monster", 0x13: "bumper",
    0x14: "pipe wall", 0x15: "movie player 1", 0x16: "movie player 2",
    0x17: "movie player 3", 0x18: "demo P1", 0x19: "demo P2", 0x1A: "demo P3",
    0x1B: "demo P4", 0x1C: "demo P5", 0x1D: "demo P6", 0x1E: "demo P7",
    0x1F: "demo P8", 0x20: "rebirth platform", 0x21: "1P player",
    0x22: "1P ally 1", 0x23: "1P ally 2", 0x24: "1P ally 3", 0x25: "1P enemy 1",
    0x26: "1P enemy 2", 0x27: "1P enemy 3", 0x28: "1P enemy 4",
    0x29: "bonus3 taru bomb", 0x2B: "1P enemy team", 0x2C: "challenger player",
}   # (smashremix_extra/stage/collision.py MAPOBJ_KIND)
BOUND_NAMES = {'blast_zones': 'Blast zones', 'camera_bounds': 'Camera bounds',
               'blast_zones_team': 'Blast zones (team)', 'camera_bounds_team': 'Camera bounds (team)'}
BOUND_COLOR = {'blast_zones': (1.0, 0.1, 0.1, 1), 'camera_bounds': (0.6, 0.6, 0.6, 1),
               'blast_zones_team': (0.6, 0.05, 0.05, 1), 'camera_bounds_team': (0.35, 0.35, 0.35, 1)}


def game_point(x, y):
    return mathutils.Vector((x, 0.0, y))


# ---------------------------------------------------------------------------- build

def _coll(name, parent=None):
    scene = bpy.context.scene
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    parent = parent or scene.collection
    if c.name not in parent.children:
        parent.children.link(c)
    c.hide_render = True               # (editing aids: never in renders)
    return c


def helper_mat(name, rgba):
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m['n64_helper'] = True             # not a model material (blend_to_gltf.py / the panel skip it)
    m.diffuse_color = rgba
    m.use_nodes = True
    bsdf = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is not None:
        bsdf.inputs['Base Color'].default_value = rgba
        bsdf.inputs['Alpha'].default_value = rgba[3]
        for k in ('Emission Color', 'Emission'):
            if k in bsdf.inputs:
                bsdf.inputs[k].default_value = rgba
                break
        if 'Emission Strength' in bsdf.inputs:
            bsdf.inputs['Emission Strength'].default_value = 0.6
    if rgba[3] < 1:
        for attr, val in (('surface_render_method', 'BLENDED'), ('blend_method', 'BLEND')):
            try:
                setattr(m, attr, val)
            except (AttributeError, TypeError):
                pass
        m.use_backface_culling = False
    return m


def line_material(ltype, flags):
    r, g, b, a = LINE_COLOR[ltype]
    if flags & FLAG_DROP:                        # drop-through: lighter
        r, g, b = (r + 1) / 2, (g + 1) / 2, (b + 1) / 2
    return helper_mat(f'N64 Collision {ltype}' + (' drop-through' if flags & FLAG_DROP else ''), (r, g, b, a))


def _cube_mesh():
    me = bpy.data.meshes.get('N64 Hurtbox')
    if me is None:
        me = bpy.data.meshes.new('N64 Hurtbox')
        v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        me.from_pydata(v, [], faces)
    return me


def build_hurtboxes(hb, arm):
    """Manifest 'hurtboxes' -> cubes on their bones."""
    c = _coll('Hurtboxes')
    me = _cube_mesh()
    if arm is not None:
        # every joint's rest matrix (game space): the panel's offset / size in game units, new boxes
        arm['n64_joint_worlds'] = {f'joint_{int(j):02d}': [v for row in W for v in row]
                                   for j, W in (hb.get('joint_worlds') or {}).items()}
        bpy.context.view_layer.update()
    for b in hb['boxes']:
        name = f'hurtbox joint_{b["joint"]:02d} {b["placement"]}'
        ob = bpy.data.objects.new(name, me.copy())
        ob.data.name = name
        c.objects.link(ob)
        ob['n64_gamedata'] = 'hurtbox'
        ob['n64_hurtbox_order'] = b['order']     # (its original place: config.yaml comments; the
                                                 # import orders the boxes itself)
        ob['n64_hurtbox_placement'] = b['placement']
        ob['n64_hurtbox_grabbable'] = bool(b['grabbable'])
        ob['n64_hurtbox_joint'] = b['joint']
        ob['n64_hurtbox_half'] = True            # (the cube is +-1 = half the size; older .blends: full)
        ob.data.materials.append(helper_mat(f'N64 Hurtbox {b["placement"]}', PLACEMENT_COLOR[b['placement']]))
        ob.show_wire = True
        ob.display_type = 'SOLID'
        W = mathutils.Matrix(b['world'])
        # size = the box's full width (the game halves it when the fighter loads: ftmanager.c)
        M = GAME_TO_BLENDER @ W @ mathutils.Matrix.Translation(b['offset']) @ \
            mathutils.Matrix.Diagonal((*(v / 2 for v in b['size']), 1.0))
        bone = f'joint_{b["joint"]:02d}'
        if arm is not None and bone in arm.data.bones:
            ob.parent, ob.parent_type, ob.parent_bone = arm, 'BONE', bone
            bpy.context.view_layer.update()
        ob.matrix_world = M
    for vl in bpy.context.scene.view_layers:     # hidden at first: the eye in the outliner shows them
        lc = vl.layer_collection.children.get(c.name)
        if lc is not None:
            lc.hide_viewport = True
    return c


def _poly_curve(name, pts, cyclic=False, width=16.0):
    cu = bpy.data.curves.new(name, 'CURVE')
    cu.dimensions = '3D'
    cu.bevel_depth = width
    cu.bevel_resolution = 0
    sp = cu.splines.new('POLY')
    sp.points.add(len(pts) - 1)
    for p, (x, y) in zip(sp.points, pts):
        p.co = (x, 0.0, y, 1.0)
    sp.use_cyclic_u = cyclic
    return cu


def set_line_look(ob):
    """The material that shows the line's type / drop-through (after a change in the panel)."""
    flags = (FLAG_DROP if ob.get('n64_drop_through') else 0)
    mat = line_material(ob.get('n64_coll_type', 'floor'), flags)
    if ob.data.materials:
        ob.data.materials[0] = mat
    else:
        ob.data.materials.append(mat)


def build_stage(sc):
    """Manifest 'stage_collision' -> one editable edge mesh per group, Stage objects."""
    root = _coll('Collision')
    groups = max([sc.get('groups') or 0] + [ln['group'] for ln in sc['lines']])
    gcolls = {g: _coll(f'Group {g}', root) for g in range(1, groups + 1)}
    type_index = {name: i for i, name in enumerate(LINE_TYPES)}
    for group, collection in gcolls.items():
        vertices, edges, start_vertices, edge_data = [], [], [], {name: [] for name in (
            COLL_EDGE_TYPE, COLL_EDGE_FLAGS, COLL_EDGE_ORDER, COLL_EDGE_SEGMENT,
            COLL_EDGE_END_FLAGS, COLL_EDGE_POINT_FLAGS)}
        vertex_ids, edge_keys = {}, set()

        def add_vertex(point, flags, ids):
            key = (int(point[0]), int(point[1]), int(flags))
            if key not in ids:
                ids[key] = len(vertices)
                vertices.append((key[0], 0.0, key[1]))
            return ids[key]

        for order, ln in enumerate(sc['lines']):
            if ln['group'] != group:
                continue
            point_flags = ln.get('point_flags') or [ln['flags']] * len(ln['points'])
            point_ids = [add_vertex(point, flags, vertex_ids)
                         for point, flags in zip(ln['points'], point_flags)]
            candidate_keys = [(min(a, b), max(a, b)) for a, b in zip(point_ids, point_ids[1:])]
            if len(set(candidate_keys)) != len(candidate_keys) or any(k in edge_keys for k in candidate_keys):
                local_ids = {}
                point_ids = [add_vertex(point, flags, local_ids)
                             for point, flags in zip(ln['points'], point_flags)]
            for segment, (start, end) in enumerate(zip(ln['points'], ln['points'][1:])):
                start_vertex, end_vertex = point_ids[segment:segment + 2]
                edges.append((start_vertex, end_vertex))
                edge_keys.add((min(start_vertex, end_vertex), max(start_vertex, end_vertex)))
                start_vertices.append(start_vertex)
                values = (type_index[ln['type']], point_flags[segment], order, segment,
                          point_flags[segment + 1], int(bool(ln.get('point_flags'))))
                for name, value in zip(edge_data, values):
                    edge_data[name].append(int(value))
        if not edges:
            continue
        me = bpy.data.meshes.new(f'Collision Group {group}')
        me.from_pydata(vertices, edges, [])
        me.update()
        for name, values in edge_data.items():
            attr = me.attributes.new(name, 'INT', 'EDGE')
            for item, value in zip(attr.data, values):
                item.value = value
        start_side = me.attributes.new(COLL_EDGE_START_SIDE, 'INT', 'EDGE')
        for i, item in enumerate(start_side.data):
            item.value = 0 if me.edges[i].vertices[0] == start_vertices[i] else 1
        ob = bpy.data.objects.new(f'Collision Group {group}', me)
        ob['n64_gamedata'] = 'collision_group'
        ob['n64_collision_group'] = group
        ob.show_in_front = True
        collection.objects.link(ob)
    st = _coll('Stage objects')
    mo = _coll('Map objects', st)
    for i, m in enumerate(sc['map_objects']):
        ob = bpy.data.objects.new(m['name'], None)
        ob['n64_order'] = i                   # (the game's array order; new ones go last)
        ob.empty_display_type = 'SINGLE_ARROW' if m['kind'] == 0x20 else 'PLAIN_AXES'
        ob.empty_display_size = 150
        ob.show_name = True
        ob.location = game_point(m['x'], m['y'])
        ob['n64_gamedata'] = 'map_object'
        ob['n64_mapobj_kind'] = m['kind']
        mo.objects.link(ob)
    for cat, pts in (sc.get('spawn_locations') or {}).items():
        sp = _coll(f'Spawn locations ({cat})', st)
        for p, (x, y) in sorted(pts.items()):
            ob = bpy.data.objects.new(f'spawn {cat} {p.upper()}', None)
            ob.empty_display_type = 'CONE'
            ob.empty_display_size = 80
            ob.rotation_euler = (3.14159265 / 2, 0, 0)       # (the cone's tip points down, at the floor)
            ob.show_name = True
            ob.location = game_point(x, y)
            ob['n64_gamedata'] = 'spawn'
            ob['n64_spawn_category'] = cat
            ob['n64_spawn_player'] = p
            sp.objects.link(ob)
            if cat != 'default':
                ob.hide_set(True)
    bc = _coll('Bounds', st)
    for k, b in (sc.get('bounds') or {}).items():
        rect = [(b['left'], b['bottom']), (b['right'], b['bottom']), (b['right'], b['top']), (b['left'], b['top'])]
        ob = bpy.data.objects.new(BOUND_NAMES[k], _poly_curve(BOUND_NAMES[k], rect, cyclic=True, width=8.0))
        ob.data.materials.append(helper_mat(f'N64 {BOUND_NAMES[k]}', BOUND_COLOR[k]))
        ob['n64_gamedata'] = 'bounds'
        ob['n64_bounds_key'] = k
        ob.show_name = True
        bc.objects.link(ob)
        if k.endswith('_team'):
            ob.hide_set(True)


def build(man):
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    if man.get('hurtboxes'):
        build_hurtboxes(man['hurtboxes'], arm)
    if man.get('stage_collision'):
        build_stage(man['stage_collision'])


# ---------------------------------------------------------------------------- collect

def _objs(kind):
    return sorted((o for o in bpy.data.objects if o.get('n64_gamedata') == kind and o.users_collection),
                  key=lambda o: o.name)


def collect_hurtboxes():
    obs = _objs('hurtbox')
    if not obs:
        return []
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    pose = arm.data.pose_position if arm else None
    if arm:                                     # the boxes on the joints' rest pose
        arm.data.pose_position = 'REST'
        bpy.context.view_layer.update()
    out, seen = [], {}
    for ob in sorted(obs, key=lambda o: (o.get('n64_hurtbox_order', 1 << 20), o.name)):
        if ob.parent_type == 'BONE' and re.match(r'joint_\d+$', ob.parent_bone or ''):
            joint = int(ob.parent_bone[6:])
        elif 'n64_hurtbox_joint' in ob.keys():
            joint = int(ob['n64_hurtbox_joint'])
        else:
            raise SystemExit(f'hurtbox {ob.name}: parent it to a joint bone (joint_NN)')
        order = ob.get('n64_hurtbox_order')
        if order is not None and order in seen:     # a duplicate (Shift+D) copied it: a new box
            order = None
        if order is not None:
            seen[order] = ob.name
        out.append(dict(name=ob.name, joint=joint, order=order,
                        placement=str(ob.get('n64_hurtbox_placement', 'mid')),
                        grabbable=bool(ob.get('n64_hurtbox_grabbable', False)),
                        half=bool(ob.get('n64_hurtbox_half', False)),
                        matrix=[list(r) for r in ob.matrix_world]))
    if arm:
        arm.data.pose_position = pose
        bpy.context.view_layer.update()
    return out


def _line_points(ob):
    M = ob.matrix_world
    pts = []
    if ob.type == 'MESH':
        if len(ob.data.vertices) < 2:
            return pts
        adjacency = [set() for _ in ob.data.vertices]
        for edge in ob.data.edges:
            a, b = edge.vertices
            adjacency[a].add(b)
            adjacency[b].add(a)
        ends = [i for i, neighbors in enumerate(adjacency) if len(neighbors) == 1]
        if len(ends) != 2 or any(len(neighbors) not in (1, 2) for neighbors in adjacency):
            raise SystemExit(f'collision line {ob.name}: mesh must be one unbranched open chain')
        ordered, seen, previous, current = [], set(), None, min(ends)
        while current not in seen:
            ordered.append(current)
            seen.add(current)
            following = [i for i in adjacency[current] if i != previous]
            if not following:
                break
            previous, current = current, following[0]
        if len(seen) != len(ob.data.vertices):
            raise SystemExit(f'collision line {ob.name}: mesh has disconnected vertices or edges')
        for i in ordered:
            v = M @ ob.data.vertices[i].co
            pts.append([int(round(v.x)), int(round(v.z))])
        return pts
    for sp in ob.data.splines:
        for p in (sp.points if sp.type == 'POLY' or sp.type == 'NURBS' else sp.bezier_points):
            v = M @ (p.co.xyz if hasattr(p.co, 'xyz') else p.co)
            pts.append([int(round(v.x)), int(round(v.z))])
        if len(ob.data.splines) > 1:
            print(f'  warning: {ob.name}: {len(ob.data.splines)} curves in one object; one line per object '
                  '(Separate them: P in Edit Mode)')
            break
    return pts


def _int_attribute(mesh, name, domain):
    attr = mesh.attributes.get(name)
    if attr is None or attr.data_type != 'INT' or attr.domain != domain:
        raise SystemExit(f'{mesh.name}: missing integer {domain.lower()} attribute {name}')
    return attr.data


def _group_mesh_lines(ob):
    me = ob.data
    type_attr = _int_attribute(me, COLL_EDGE_TYPE, 'EDGE')
    flags_attr = _int_attribute(me, COLL_EDGE_FLAGS, 'EDGE')
    order_attr = _int_attribute(me, COLL_EDGE_ORDER, 'EDGE')
    segment_attr = _int_attribute(me, COLL_EDGE_SEGMENT, 'EDGE')
    start_side_attr = _int_attribute(me, COLL_EDGE_START_SIDE, 'EDGE')
    end_flags_attr = _int_attribute(me, COLL_EDGE_END_FLAGS, 'EDGE')
    point_flags_attr = _int_attribute(me, COLL_EDGE_POINT_FLAGS, 'EDGE')
    group = next((int(c.name[6:]) for c in ob.users_collection if re.match(r'Group \d+$', c.name)), None)
    if group is None:
        raise SystemExit(f'{ob.name}: put it in a Collision > Group N collection')

    records, by_line = [], {}
    for i, edge in enumerate(me.edges):
        a, b = edge.vertices
        start, end = (a, b) if int(start_side_attr[i].value) == 0 else (b, a)
        typ = int(type_attr[i].value)
        if not 0 <= typ < len(LINE_TYPES):
            raise SystemExit(f'{ob.name}: edge {i} has invalid collision type {typ}')
        start_world = ob.matrix_world @ me.vertices[start].co
        end_world = ob.matrix_world @ me.vertices[end].co
        record = dict(index=i, start_vertex=start, end_vertex=end, start=(int(round(start_world.x)),
                       int(round(start_world.z))), end=(int(round(end_world.x)), int(round(end_world.z))),
                      type=LINE_TYPES[typ], flags=int(flags_attr[i].value),
                      end_flags=int(end_flags_attr[i].value), order=int(order_attr[i].value),
                      segment=int(segment_attr[i].value), has_point_flags=bool(point_flags_attr[i].value))
        records.append(record)
        if record['order'] >= 0 and record['segment'] >= 0:
            by_line.setdefault(record['order'], []).append(record)

    def as_line(run, order):
        point_flags = [edge['flags'] for edge in run] + [run[-1]['end_flags']]
        out = dict(group=group, type=run[0]['type'],
                   points=[run[0]['start']] + [edge['end'] for edge in run],
                   flags=point_flags[0], order=order)
        if any(edge['has_point_flags'] for edge in run) or len(set(point_flags)) > 1:
            out['point_flags'] = point_flags
        return out

    result, represented = [], set()
    for order, line_records in sorted(by_line.items()):
        line_records.sort(key=lambda edge: edge['segment'])
        segment_ids = [edge['segment'] for edge in line_records]
        connected = all(a['end_vertex'] == b['start_vertex']
                        for a, b in zip(line_records, line_records[1:]))
        if segment_ids != list(range(len(line_records))) or not connected:
            continue
        runs, current = [], []
        for edge in line_records:
            if current and edge['type'] != current[-1]['type']:
                runs.append(current)
                current = []
            current.append(edge)
        if current:
            runs.append(current)
        for i, run in enumerate(runs):
            result.append(as_line(run, order if i == 0 else None))
        represented.update(edge['index'] for edge in line_records)

    for edge in records:
        if edge['index'] in represented:
            continue
        result.append(as_line([edge], None))
    return result


def collect_stage():
    lines, seen = [], set()
    for ob in sorted(_objs('collision_group'), key=lambda o: (o.get('n64_collision_group', 99), o.name)):
        lines.extend(_group_mesh_lines(ob))
    seen.update(ln['order'] for ln in lines if ln.get('order') is not None)
    for ob in sorted(_objs('collision'), key=lambda o: (o.get('n64_order', 1 << 20), o.name)):
        group = next((int(c.name[6:]) for c in ob.users_collection if re.match(r'Group \d+$', c.name)), None)
        if group is None:
            raise SystemExit(f'collision line {ob.name}: put it in a Collision > Group N collection')
        pts = _line_points(ob)
        if len(pts) < 2:
            print(f'  warning: {ob.name}: fewer than 2 points, skipped')
            continue
        fl = (FLAG_DROP if ob.get('n64_drop_through') else 0) | (FLAG_LEDGE if ob.get('n64_ledge') else 0) | \
            (int(ob.get('n64_surface', 0)) & 0xFF)
        pf = list(ob['n64_point_flags']) if 'n64_point_flags' in ob.keys() else None
        if pf and (len(pf) != len(pts) or pf[0] != fl):     # edited: the line's flags for every point
            pf = None
        order = ob.get('n64_order')
        if order in seen:                       # a copy (Shift+D): a new line
            order = None
        seen.add(order)
        lines.append(dict(group=group, type=str(ob.get('n64_coll_type', 'floor')), points=pts, flags=fl,
                          point_flags=pf, order=order))
    mapobjs = []
    seen = set()
    obs = []
    for ob in sorted(_objs('map_object'), key=lambda o: (o.get('n64_order', 1 << 20), o.name)):
        k = ob.get('n64_order')
        obs.append(((1 << 20) if k is None or k in seen else k, len(obs), ob))   # (a copy: after the others)
        seen.add(k)
    for _k, _i, ob in sorted(obs, key=lambda t: t[:2]):
        v = ob.matrix_world.translation
        mapobjs.append(dict(kind=int(ob['n64_mapobj_kind']), x=int(round(v.x)), y=int(round(v.z)), name=ob.name))
    spawns = {}
    for ob in _objs('spawn'):
        v = ob.matrix_world.translation
        spawns.setdefault(str(ob['n64_spawn_category']), {})[str(ob['n64_spawn_player'])] = \
            [int(round(v.x)), int(round(v.z))]
    bounds = {}
    for ob in _objs('bounds'):
        M = ob.matrix_world
        vs = [M @ p.co.xyz for sp in ob.data.splines for p in sp.points]
        if vs:
            bounds[str(ob['n64_bounds_key'])] = dict(top=int(round(max(v.z for v in vs))),
                                                     bottom=int(round(min(v.z for v in vs))),
                                                     left=int(round(min(v.x for v in vs))),
                                                     right=int(round(max(v.x for v in vs))))
    return dict(lines=lines, map_objects=mapobjs, spawn_locations=spawns, bounds=bounds)


def collect():
    """gamedata.json contents (only what the .blend has)."""
    out = {}
    if any(o.get('n64_gamedata') == 'hurtbox' for o in bpy.data.objects) or bpy.data.collections.get('Hurtboxes'):
        out['hurtboxes'] = collect_hurtboxes()
    if bpy.data.collections.get('Collision') or bpy.data.collections.get('Stage objects'):
        out['stage_collision'] = collect_stage()
    return out
