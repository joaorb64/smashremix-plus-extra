"""Fighter joints in Blender: removing joints and renumbering the rest (Bone > N64 Joint >
Remove Joint), and undoing the renumbering for the import (blend_to_gltf.py).

A fighter's joint bones are joint_NN, NN = its DObjDesc index (game joint NN + 4): the game
numbers joints in that order, so when one goes, every joint after it moves down. Remove Joint
deletes the joint (and its branch), its meshes (body / costumes / low detail / model parts /
electric skeleton) and hurtboxes, then renames the joints after it so the bones show the
numbers the game will use (moveset events name joints by them). Each bone keeps the joint it
was in the export (n64_joint_orig, stamped before the first removal); the removed joints'
animation curves stay, renamed n64_removed_joint_NN, so the actions still compare equal to the
export's (unedited animations are then copied exactly, minus the removed joints' entries).

blend_to_gltf.py renames everything back to the export's names (in memory) before it reads the
scene, and reports the joints that are gone: import_fighter.py renumbers the files (jointmap.py).
"""
import re

import bpy

JOINT_RE = re.compile(r'joint_(\d+)$')
REMOVED_PREFIX = 'n64_removed_joint_'
# armature properties holding a joint bone's name (Armature > N64 Fighter Joints)
ARM_BONE_PROPS = ['n64_item_heavy', 'n64_item_light', 'n64_foot_r', 'n64_foot_l', 'n64_access_joint'] + \
    [f'n64_effect_joint_{i}' for i in range(5)] + [f'n64_texpart_{k}_joint' for k in range(2)]
# objects on a joint: body / costume copies, low detail, model part variants, electric skeleton
OBJ_RE = re.compile(r'^(hi|lo|part\d+_hi|part\d+_lo|skel\d+)_j(\d+)((?:_c\d+)?(?:\.\d+)?)$')


def fighter_armature():
    return next((o for o in bpy.data.objects if o.type == 'ARMATURE' and not o.get('n64_reference')
                 and 'n64_joint_attrs_orig' in o.data.keys()), None)


def joint_index(name):
    m = JOINT_RE.match(name or '')
    return int(m.group(1)) if m else None


def orig_index(b):
    """The joint the bone was in the export."""
    if 'n64_joint_orig' in b.keys():
        return int(b['n64_joint_orig'])
    return joint_index(b.name)


def fighter_actions():
    return [a for a in bpy.data.actions if not a.get('n64_reference')]


def _fcurves(act):
    import keyreduce as KR
    return KR.action_fcurves(act)


_PATH_RE = re.compile(r'pose\.bones\["([^"]+)"\]')


def rename(arm, names):
    """Rename joint bones: names {old bone name: new name} (bones in it that don't exist: only
    their animation curves are renamed - removed joints' leftovers). Updates vertex groups,
    animation curves of every fighter action, objects named after joints, bone-name properties."""
    names = {o: n for o, n in names.items() if o != n}
    if not names:
        return
    # animation curves: every fighter action, in one pass (the armature's own action is
    # unassigned meanwhile, so Blender's automatic renaming leaves it to this)
    ad_ = arm.animation_data
    keep = None
    if ad_ is not None:
        keep = (ad_.action, getattr(ad_, 'action_slot', None))
        ad_.action = None
    for act in fighter_actions():
        for fc in _fcurves(act):
            new = _PATH_RE.sub(lambda m: f'pose.bones["{names.get(m.group(1), m.group(1))}"]', fc.data_path)
            if new != fc.data_path:
                fc.data_path = new
            if fc.group is not None and fc.group.name in names:
                fc.group.name = names[fc.group.name]
    # bones (Blender renames the meshes' vertex groups, bone parenting and constraint targets);
    # through temporary names: the new names are the old names of others
    bones = arm.data.bones
    todo = [(o, n) for o, n in names.items() if o in bones]
    for o, _ in todo:
        bones[o].name = '__n64_tmp__' + o
    for o, n in todo:
        bones['__n64_tmp__' + o].name = n
    if keep is not None:
        ad_.action = keep[0]
        if keep[1] is not None and hasattr(ad_, 'action_slot'):
            try:
                ad_.action_slot = keep[1]
            except (TypeError, AttributeError, RuntimeError):
                pass
    # objects named after joints
    idx = {joint_index(o): joint_index(n) for o, n in names.items()
           if joint_index(o) is not None and joint_index(n) is not None}
    objs = []
    for ob in bpy.data.objects:
        m = OBJ_RE.match(ob.name)
        if m and int(m.group(2)) in idx:
            objs.append((ob, f'{m.group(1)}_j{idx[int(m.group(2))]:02d}{m.group(3)}'))
    for ob in bpy.data.objects:              # (a hurtbox not parented to its bone names it by number)
        if ob.get('n64_gamedata') == 'hurtbox' and 'n64_hurtbox_joint' in ob.keys() and \
                int(ob['n64_hurtbox_joint']) in idx:
            ob['n64_hurtbox_joint'] = idx[int(ob['n64_hurtbox_joint'])]
    for ob, _ in objs:
        ob.name = '__n64_tmp__' + ob.name
    for ob, n in objs:
        ob.name = n
    for ob in bpy.data.objects:              # (mesh data shares the object's name)
        if ob.type == 'MESH' and ob.data.users == 1 and OBJ_RE.match(ob.name):
            ob.data.name = ob.name
    # bone names in properties
    ad = arm.data
    if 'n64_joint_origs' in ad.keys():
        ad['n64_joint_origs'] = {names.get(n, n): v for n, v in dict(ad['n64_joint_origs']).items()
                                 if names.get(n, n).startswith('joint_')}
    for p in ARM_BONE_PROPS:
        if p in ad.keys() and ad[p] in names:
            ad[p] = names[ad[p]]
    for b in ad.bones:
        if b.get('n64_hidden_parent') in names:
            b['n64_hidden_parent'] = names[b['n64_hidden_parent']]


def branch(arm, name):
    """The bone and every joint bone under it."""
    out = []

    def walk(b):
        out.append(b.name)
        for c in b.children:
            if joint_index(c.name) is not None:
                walk(c)
    walk(arm.data.bones[name])
    return out


def joint_objects(idx):
    """Objects on joint idx (current numbering)."""
    return [ob for ob in bpy.data.objects
            if (m := OBJ_RE.match(ob.name)) and int(m.group(2)) == idx]


ROLE_TEXT = {'n64_item_heavy': 'heavy item joint', 'n64_item_light': 'light item joint',
             'n64_foot_r': 'right foot IK thigh', 'n64_foot_l': 'left foot IK thigh',
             'n64_access_joint': 'accessory part', **{f'n64_effect_joint_{i}': f'effect joint {i + 1}' for i in range(5)},
             **{f'n64_texpart_{k}_joint': f'alternate texture {k + 1}' for k in range(2)}}


def _new_names(arm, gone):
    rest = sorted((joint_index(b.name), b.name) for b in arm.data.bones
                  if joint_index(b.name) is not None and b.name not in gone)
    return {nm: f'joint_{k:02d}' for k, (_, nm) in enumerate(rest)}


def affected_specials(arm, name):
    """Removing `name` (and its branch): the special joints (Armature > N64 Fighter Joints) whose
    number changes -> [(role, bone now, its name after)]."""
    ad = arm.data
    new = _new_names(arm, branch(arm, name))
    out = []
    for p in ARM_BONE_PROPS:
        v = ad.get(p) if p in ad.keys() else None
        if v and v in new and new[v] != v:
            out.append((ROLE_TEXT.get(p, p), v, new[v]))
    for b in ad.bones:
        if int(b.get('n64_hidden_index', -1)) >= 0 and b.name in new and new[b.name] != b.name:
            out.append((f'hidden part {b["n64_hidden_index"]}', b.name, new[b.name]))
    return out


def _remove(context, arm, name, keep_numbers=False):
    """Bone > N64 Joint > Remove Joint. keep_numbers: the special joints (Armature > N64 Fighter
    Joints) keep their joint numbers, landing on the bones that take them, instead of following
    their bones. -> (ok, message)."""
    ad = arm.data
    gone = branch(arm, name)
    before = {p: ad[p] for p in ARM_BONE_PROPS if p in ad.keys() and ad[p]}
    if keep_numbers:
        after = set(_new_names(arm, gone).values()) | {b.name for b in ad.bones if joint_index(b.name) is None}
        lost = [f'{ROLE_TEXT.get(p, p)} ({v})' for p, v in before.items() if v not in after]
        if lost:
            return False, f'No joint keeps the number of: {", ".join(lost)} - choose Follow Their Bones'
    for n in gone:
        b = ad.bones[n]
        if int(b.get('n64_hidden_index', -1)) >= 0:
            return False, f'{n} is a hidden part: make it a normal joint first (Remove Hidden Part)'
        used = [p[4:] for p in ARM_BONE_PROPS if ad.get(p) == n]
        if used:
            return False, (f'{n} is the fighter\'s {", ".join(used)} (Armature > N64 Fighter Joints): '
                           'point that at another joint first')
        if any(bb.get('n64_hidden_parent') == n for bb in ad.bones):
            return False, f'{n} is a hidden part\'s parent: change that first'
    gone_idx = {joint_index(n) for n in gone}
    # meshes of other joints must not be weighted to them
    dead_objs = [ob for i in gone_idx for ob in joint_objects(i)]
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or ob in dead_objs:
            continue
        vgs = {vg.index: vg.name for vg in ob.vertex_groups if vg.name in gone}
        if vgs and any(g.group in vgs and g.weight > 0 for v in ob.data.vertices for g in v.groups):
            return False, f'{ob.name} has vertices weighted to {", ".join(sorted(set(vgs.values())))}: reweight them first'
    hurt = [ob for ob in bpy.data.objects if ob.get('n64_gamedata') == 'hurtbox' and ob.parent == arm
            and ob.parent_bone in gone]
    stamp(arm)                         # (the export's joint numbers, before the first change)
    removed = list(ad.get('n64_removed_joints', []))
    removed += [orig_index(ad.bones[n]) for n in gone]
    n_objs = len(dead_objs)
    for ob in dead_objs + hurt:
        bpy.data.objects.remove(ob)
    # the removed bones' (unused) vertex groups go too: the renamed bones' groups take their names
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            for vg in [vg for vg in ob.vertex_groups if vg.name in gone]:
                ob.vertex_groups.remove(vg)
    # the bones
    prev_mode, prev_active = arm.mode, context.view_layer.objects.active
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    origs = {n: orig_index(ad.bones[n]) for n in gone}
    for n in gone:
        eb = arm.data.edit_bones.get(n)
        if eb is not None:
            arm.data.edit_bones.remove(eb)
    bpy.ops.object.mode_set(mode='OBJECT')
    if prev_mode == 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    context.view_layer.objects.active = prev_active
    ad['n64_removed_joints'] = sorted(set(removed))
    # renumber: the joints keep their order, without gaps
    names = _new_names(arm, ())
    # the removed joints' curves: out of the way of the renamed ones
    for n, o in origs.items():
        names[n] = f'{REMOVED_PREFIX}{o:02d}'
    rename(arm, names)
    moved = sum(1 for o, n in names.items() if n.startswith('joint_') and o != n)
    stamp(arm)
    remember(arm)
    if keep_numbers:                   # (rename moved them with their bones: back to the numbers)
        for p, v in before.items():
            ad[p] = v
    try:
        import n64_panel
        n64_panel.color_special_joints(arm, force=True)
    except Exception:            # (the viewport colors only)
        pass
    return True, (f'Removed {", ".join(gone)} ({n_objs} objects, {len(hurt)} hurtboxes); '
                  f'{moved} joints renumbered')


_BUSY = [0]          # a button (Add / Remove Joint, Put Meshes) at work: the update handler's sync waits


class busy:
    def __enter__(self):
        _BUSY[0] += 1

    def __exit__(self, *a):
        _BUSY[0] -= 1


def stamp(arm):
    """Before the first joint edit: each bone remembers its export joint number (and the armature
    a copy by name: a bone deleted in Edit Mode loses its own)."""
    for b in arm.data.bones:
        if joint_index(b.name) is not None and 'n64_joint_orig' not in b.keys():
            b['n64_joint_orig'] = joint_index(b.name)
    arm.data['n64_joint_origs'] = {b.name: int(b['n64_joint_orig']) for b in arm.data.bones
                                   if joint_index(b.name) is not None and 'n64_joint_orig' in b.keys()}


def _add(context, arm, parent_name):
    """Bone > N64 Joint > Add Joint: a new joint under parent_name, stored after the parent's
    other descendants (DObjDesc order is depth first); the joints after it move up one.
    -> (ok, message, new bone name)."""
    ad = arm.data
    par = ad.bones.get(parent_name)
    if par is None or (joint_index(parent_name) is None):
        return False, 'Select a joint bone (joint_NN) to add the new joint under', None
    if int(par.get('n64_hidden_index', -1)) >= 0:
        return False, f'{parent_name} is a hidden part: add the joint under a normal joint', None
    n = sum(1 for b in ad.bones if joint_index(b.name) is not None)
    if n + 4 >= 37:
        return False, 'The game has room for 37 joints (33 DObjDesc entries)', None
    stamp(arm)
    at = max(joint_index(x) for x in branch(arm, parent_name)) + 1
    names = {b.name: f'joint_{joint_index(b.name) + 1:02d}' for b in ad.bones
             if joint_index(b.name) is not None and joint_index(b.name) >= at}
    rename(arm, names)
    new = f'joint_{at:02d}'
    prev_mode, prev_active = arm.mode, context.view_layer.objects.active
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    pe = arm.data.edit_bones[parent_name]
    eb = arm.data.edit_bones.new(new)
    eb.head = pe.head.copy()
    eb.tail = pe.head + (pe.tail - pe.head).normalized() * max(pe.length * 0.5, 15.0)
    eb.parent = pe
    eb.use_connect = False
    for c in pe.collections if hasattr(pe, 'collections') else []:
        c.assign(eb)
    bpy.ops.object.mode_set(mode='OBJECT')
    if prev_mode == 'POSE':
        bpy.ops.object.mode_set(mode='POSE')
    context.view_layer.objects.active = prev_active
    b = ad.bones[new]
    b['n64_joint_orig'] = -1                 # (new: no export joint)
    b['n64_setup'] = True
    b['n64_animlock'] = False
    b['n64_hidden_index'] = -1
    ad['n64_added_joints'] = int(ad.get('n64_added_joints', 0)) + 1
    stamp(arm)
    remember(arm)
    moved = len(names)
    return True, (f'Added {new} under {parent_name} (at its head: move it in Edit Mode); {moved} joints '
                  'renumbered'), new


def put_meshes(context, arm, bone_name, objs):
    """Bone > N64 Joint > Put Selected Meshes Here: each mesh becomes the joint's body piece
    (hi_jNN, Costume 0 + a copy in every other costume; in Low detail: lo_jNN), skinned only to
    the joint. -> (ok, message)."""
    idx = joint_index(bone_name)
    if idx is None:
        return False, 'Select a joint bone (joint_NN)'
    done = []
    for ob in objs:
        if ob.type != 'MESH':
            continue
        lo = any(c.name == 'Low detail' for c in ob.users_collection)
        det = 'lo' if lo else 'hi'
        name = f'{det}_j{idx:02d}'
        other = bpy.data.objects.get(name)
        if other is not None and other is not ob:
            return False, f'{name} exists already: join the meshes into it (Ctrl+J) instead'
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        ob.name = name
        ob.data.name = name
        # skinned to this joint only
        mw = ob.matrix_world.copy()
        for vg in list(ob.vertex_groups):
            ob.vertex_groups.remove(vg)
        vg = ob.vertex_groups.new(name=bone_name)
        vg.add(list(range(len(ob.data.vertices))), 1.0, 'REPLACE')
        mod = next((m for m in ob.modifiers if m.type == 'ARMATURE'), None) or ob.modifiers.new('Armature', 'ARMATURE')
        mod.object = arm
        ob.parent = arm
        ob.parent_type = 'OBJECT'
        ob.matrix_world = mw
        target = bpy.data.collections.get('Low detail' if lo else 'Costume 0')
        if target is not None:
            for c in list(ob.users_collection):
                if c is not target:
                    c.objects.unlink(ob)
            if target not in ob.users_collection:
                target.objects.link(ob)
        if not lo:
            # every other costume shows it too: linked copies with that costume's materials
            for c in bpy.data.collections:
                m = re.match(r'Costume (\d+)$', c.name)
                if not m or m.group(1) == '0':
                    continue
                k = int(m.group(1))
                cname = f'{name}_c{k}'
                old = bpy.data.objects.get(cname)
                if old is not None:
                    bpy.data.objects.remove(old)
                d = ob.copy()
                d.name = cname
                c.objects.link(d)
                for slot in d.material_slots:
                    base = slot.material
                    if base is None:
                        continue
                    slot.link = 'OBJECT'
                    slot.material = bpy.data.materials.get(f'{re.sub(r"_c[0-9]+$", "", base.name)}_c{k}') or base
        done.append(name)
    if not done:
        return False, 'Select the mesh objects to put on the joint'
    # no low detail piece on the joint: the high one's copy (the game draws the low detail model
    # far away / in some modes: without it the piece would vanish there). Edit it down there.
    lo_coll = bpy.data.collections.get('Low detail')
    hi = bpy.data.objects.get(f'hi_j{idx:02d}')
    if hi is not None and lo_coll is not None and bpy.data.objects.get(f'lo_j{idx:02d}') is None:
        lo = hi.copy()
        lo.data = hi.data.copy()
        lo.name = lo.data.name = f'lo_j{idx:02d}'
        for slot in lo.material_slots:
            slot.link = 'DATA'
        lo_coll.objects.link(lo)
        done.append(f'{lo.name} (copy of {hi.name})')
    return True, f'{", ".join(done)} on {bone_name}'


def restore_export_names(arm):
    """blend_to_gltf.py: rename everything back to the export's joint numbers (in memory). Added
    joints get temporary numbers after the export's. -> dict(order: [export joint number (or
    temporary) per current joint, in order], added: {temporary: dict(parent (export / temporary
    number, None: the root), head (game-space position), setup, animlock)})."""
    ad = arm.data
    joints = sorted((joint_index(b.name), b) for b in ad.bones if joint_index(b.name) is not None)
    origs = [o_ for _, b in joints for o_ in [int(b.get('n64_joint_orig', joint_index(b.name)))] if o_ >= 0]
    n_export = max(origs + [int(x) for x in ad.get('n64_removed_joints', [])] + [-1]) + 1
    names, order, added = {}, [], {}
    temp = n_export
    numbers = {}
    for _, b in joints:
        o = int(b['n64_joint_orig']) if 'n64_joint_orig' in b.keys() else joint_index(b.name)
        if o < 0:
            o, temp = temp, temp + 1
        numbers[b.name] = o
        names[b.name] = f'joint_{o:02d}'
        order.append(o)
    mw = arm.matrix_world
    for _, b in joints:
        o = numbers[b.name]
        if o >= n_export:
            p = mw @ b.head_local
            added[o] = dict(parent=numbers.get(b.parent.name) if b.parent is not None else None,
                            head=[float(p.x), float(p.z), float(-p.y)],          # Blender (x, -z, y) = game (x, y, z)
                            setup=bool(b.get('n64_setup', True)), animlock=bool(b.get('n64_animlock', False)))
    for act in fighter_actions():
        for fc in _fcurves(act):
            for m in _PATH_RE.finditer(fc.data_path):
                if m.group(1).startswith(REMOVED_PREFIX):
                    names[m.group(1)] = 'joint_' + m.group(1)[len(REMOVED_PREFIX):]
    rename(arm, names)
    return dict(order=order, added=added, n_export=n_export)


# ---------------------------------------------------------------------------- Blender's own bone edits
# Deleting a joint bone (X) or adding a bone (E / Shift+A) in Edit Mode works like Remove Joint /
# Add Joint once the armature leaves Edit Mode (n64_panel's update handler runs sync): the bones are
# renumbered, the removed joint's meshes go, a new bone gets the next number in its parent's branch.
ROOT_BONES = ('TopN', 'TransN', 'XRotN', 'YRotN')


def remember(arm):
    """The joints as they are now: {export number (or -1 - k: new): current index} (sync compares)."""
    cur = {}
    for b in arm.data.bones:
        i = joint_index(b.name)
        if i is not None and 'n64_joint_orig' in b.keys():
            cur[b.name] = i
    arm.data['n64_joint_state'] = {n: i for n, i in cur.items()}
    arm.data['n64_joint_parents'] = {n: (arm.data.bones[n].parent.name if arm.data.bones[n].parent else '')
                                     for n in cur}


def sync(context, arm):
    """After Edit Mode: renumber for bones deleted / added with Blender's own tools. -> a message or None."""
    if _BUSY[0]:
        return None
    with busy():                       # (re-expressing curves sets frames: nested updates wait)
        return _sync(context, arm)


def _sync(context, arm):
    ad = arm.data
    if 'n64_joint_state' not in ad.keys():
        stamp(arm)
        remember(arm)
        return None
    was = dict(ad['n64_joint_state'])          # bone name -> its index then
    have = {b.name for b in ad.bones}
    gone = [n for n in was if n not in have]
    new = [b for b in ad.bones if b.name not in was and b.name not in ROOT_BONES
           and ('n64_joint_orig' not in b.keys() or joint_index(b.name) is None)]
    if 'n64_joint_parents' not in ad.keys():    # (a state from before parents were tracked)
        ad['n64_joint_parents'] = {n: (ad.bones[n].parent.name if ad.bones[n].parent else '')
                                   for n in was if n in ad.bones}
    pw = dict(ad.get('n64_joint_parents', {}))
    moved = [n for n in was if n in have and n in pw and
             (ad.bones[n].parent.name if ad.bones[n].parent else '') != pw[n]]
    if not gone and not new and not moved:
        return None
    for n in moved:
        # FOLLOW (Alt+P, the default): its keys stay as they are, relative to its rest - it follows the new
        # parent. KEEP (Set Parent's other choice): re-expressed so it moves in the world as before.
        b = ad.bones[n]
        mode = b.get('n64_reparent_next', 'FOLLOW')
        b['n64_reparent_mode'] = mode
        b.pop('n64_reparent_next', None) if 'n64_reparent_next' in b.keys() else None
        old_p = pw[n]
        if mode == 'KEEP' and old_p and old_p in have:
            reexpress(context, arm, n, old_p)
    # removed joints: their meshes and hurtboxes go, their curves out of the way
    dead_idx = {was[n] for n in gone}
    for ob in [ob for i in dead_idx for ob in joint_objects(i)]:
        bpy.data.objects.remove(ob)
    for ob in [ob for ob in bpy.data.objects if ob.get('n64_gamedata') == 'hurtbox' and ob.parent == arm
               and ob.parent_bone in gone]:
        bpy.data.objects.remove(ob)
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            for vg in [vg for vg in ob.vertex_groups if vg.name in gone]:
                ob.vertex_groups.remove(vg)
    # the export numbers of the removed ones (stamped on the bones, which are gone: kept beside)
    origs = dict(ad.get('n64_joint_origs', {}))
    removed = list(ad.get('n64_removed_joints', []))
    names = {}
    for n in gone:
        o = int(origs.get(n, was[n]))
        if o >= 0:
            removed.append(o)
            names[n] = f'{REMOVED_PREFIX}{o:02d}'
    ad['n64_removed_joints'] = sorted(set(removed))
    for b in new:
        b['n64_joint_orig'] = -1
        b['n64_setup'] = True
        b['n64_animlock'] = False
        b['n64_hidden_index'] = -1
    order = dfs_order(arm, {n: was[n] for n in was if n in have}, [b.name for b in new])
    for k, nm in enumerate(order):
        names[nm] = f'joint_{k:02d}'
    new_names = [names.get(b.name, b.name) for b in new]
    # new bones' names may clash with the targets: through temporary names (rename does)
    rename(arm, names)
    ad['n64_joint_origs'] = {f'joint_{k:02d}': int(ad.bones[f'joint_{k:02d}'].get('n64_joint_orig', -1))
                             for k in range(len(order))}
    remember(arm)
    msg = []
    if gone:
        msg.append(f'removed {", ".join(gone)}')
    if new:
        msg.append('added ' + ', '.join(new_names))
    if moved:
        msg.append('moved ' + ', '.join(f'{names.get(n, n)} (under {ad.bones[names.get(n, n)].parent.name})'
                                        for n in moved))
    moved = sum(1 for nm in order if names[nm] != nm)
    return f'Joints {"; ".join(msg)}: {moved} renumbered'


def add(context, arm, parent_name):
    with busy():
        r = _add(context, arm, parent_name)
    remember(arm)
    return r


def remove(context, arm, name, keep_numbers=False):
    with busy():
        r = _remove(context, arm, name, keep_numbers)
    remember(arm)
    return r


def dfs_order(arm, index, extra=()):
    """The joint bones in DObjDesc order (depth first: each joint's branch right after it), children
    in their current order (index: bone name -> current number; extra: new bones, after their
    siblings)."""
    joints = set(index) | set(extra)
    key = {n: (index[n] if n in index else 1000 + list(extra).index(n)) for n in joints}
    bones = arm.data.bones
    kids = {}
    roots = []
    for n in joints:
        p = bones[n].parent
        while p is not None and p.name not in joints:
            p = p.parent                         # (under a non-joint bone: TransN / XRotN...)
        bp = bones[n].parent
        if p is None or bp is None or bp.name in ROOT_BONES:
            roots.append(n)
        else:
            kids.setdefault(p.name, []).append(n)
    order = []

    def walk(n):
        order.append(n)
        for c in sorted(kids.get(n, []), key=key.get):
            walk(c)
    for r in sorted(roots, key=key.get):
        walk(r)
    return order


def _remove_bone_curves(act, bone):
    tag = f'pose.bones["{bone}"]'
    if hasattr(act, 'layers') and act.layers:
        for layer in act.layers:
            for strip in layer.strips:
                for cb in getattr(strip, 'channelbags', []):
                    for fc in [fc for fc in cb.fcurves if fc.data_path.startswith(tag)]:
                        cb.fcurves.remove(fc)
    else:
        for fc in [fc for fc in getattr(act, 'fcurves', []) if fc.data_path.startswith(tag)]:
            act.fcurves.remove(fc)


def export_name_map(arm):
    """{current bone name: the name blend_to_gltf gives it (the export's joint name)} - as
    restore_export_names would rename now (curves of removed joints included)."""
    ad = arm.data
    joints = sorted((joint_index(b.name), b) for b in ad.bones if joint_index(b.name) is not None)
    origs = [o_ for _, b in joints for o_ in [int(b.get('n64_joint_orig', joint_index(b.name)))] if o_ >= 0]
    temp = max(origs + [int(x) for x in ad.get('n64_removed_joints', [])] + [-1]) + 1
    out = {}
    for _, b in joints:
        o = int(b['n64_joint_orig']) if 'n64_joint_orig' in b.keys() else joint_index(b.name)
        if o < 0:
            o, temp = temp, temp + 1
        out[b.name] = f'joint_{o:02d}'
    return out


def export_signature(act, names, bone=None):
    """keyreduce.action_signature as blend_to_gltf will compute it (the curves under the export's
    joint names). bone: only that (current) bone's curves."""
    import hashlib
    import keyreduce as KR

    def path(fc):
        m = _PATH_RE.search(fc.data_path)
        if not m:
            return fc.data_path
        n = m.group(1)
        n2 = names.get(n) or ('joint_' + n[len(REMOVED_PREFIX):] if n.startswith(REMOVED_PREFIX) else n)
        return fc.data_path.replace(f'pose.bones["{n}"]', f'pose.bones["{n2}"]', 1)
    fcs = [fc for fc in KR.action_fcurves(act) if bone is None or KR._bone_of(fc)[0] == bone]
    h = hashlib.sha1()
    for fc in sorted(fcs, key=lambda c: (path(c), c.array_index)):
        h.update(f'{path(fc)}[{fc.array_index}]'.encode())
        for kp in fc.keyframe_points:
            h.update(('%.4f %.6f %.4f %.6f %.4f %.6f %s;' % (
                kp.co.x, kp.co.y, kp.handle_left.x, kp.handle_left.y, kp.handle_right.x, kp.handle_right.y,
                kp.interpolation)).encode())
    return h.hexdigest()


def reexpress(context, arm, bone, old_parent):
    """A bone moved to another parent (rest kept: Alt+P Keep Offset): in every fighter action, its keys
    re-expressed under the new parent so it moves as before (a key per frame). Actions unedited
    before stay unedited (their export signature follows), so the import treats them as the
    files' own (the joint's script recomputed there, the other joints byte for byte)."""
    import keyreduce as KR
    import blend_gamespace as BGS
    sc = context.scene
    ad_ = arm.animation_data or arm.animation_data_create()
    keep = (ad_.action, getattr(ad_, 'action_slot', None), sc.frame_current)
    pb = arm.pose.bones[bone]
    b = pb.bone
    op = arm.pose.bones[old_parent]
    np_ = pb.parent
    rot_path = {'QUATERNION': 'rotation_quaternion', 'AXIS_ANGLE': 'rotation_axis_angle'}.get(pb.rotation_mode,
                                                                                               'rotation_euler')
    n = 0
    names = export_name_map(arm)
    ebone = names.get(bone, bone)
    for act in fighter_actions():
        if not KR.action_fcurves(act):
            continue
        ex = act.get('n64_export')
        unedited = ex is not None and ex.get('keys') == export_signature(act, names)
        bsig = dict(act['n64_bone_signatures']) if 'n64_bone_signatures' in act.keys() else None
        bone_unedited = bsig is not None and bsig.get(ebone) == export_signature(act, names, bone)
        ad_.action = act
        if hasattr(ad_, 'action_slot') and ad_.action_slot is None and getattr(act, 'slots', None):
            ad_.action_slot = act.slots[0]
        f0, f1 = BGS.key_range(act)
        bases = []
        for fr in range(f0, f1 + 1):
            sc.frame_set(fr)
            pose = b.convert_local_to_pose(pb.matrix_basis.copy(), b.matrix_local, parent_matrix=op.matrix,
                                           parent_matrix_local=op.bone.matrix_local)
            bases.append((fr, b.convert_local_to_pose(pose, b.matrix_local, parent_matrix=np_.matrix,
                                                      parent_matrix_local=np_.bone.matrix_local, invert=True)))
        _remove_bone_curves(act, bone)
        for fr, M in bases:
            pb.matrix_basis = M
            for path in ('location', rot_path, 'scale'):
                pb.keyframe_insert(path, frame=fr, group=bone)
        for fc in KR.action_fcurves(act):
            fc.update()                              # (handles as they'll be read)
        if unedited:
            ex['keys'] = export_signature(act, names)
        if bone_unedited:
            bsig[ebone] = export_signature(act, names, bone)
            act['n64_bone_signatures'] = bsig
        n += 1
    ad_.action = keep[0]
    if keep[1] is not None and hasattr(ad_, 'action_slot'):
        try:
            ad_.action_slot = keep[1]
        except (TypeError, AttributeError, RuntimeError):
            pass
    sc.frame_set(keep[2])
    print(f'{bone}: keys re-expressed under {np_.name} in {n} actions')
    return n


def set_parent(context, arm, name, parent, mode='FOLLOW'):
    """Bone > N64 Joint > Set Parent: the joint (and its branch) under another joint, where it is
    (Keep Offset); its curves follow (reexpress) and the joints are renumbered. -> (ok, message)."""
    ad = arm.data
    if name not in ad.bones or parent not in ad.bones:
        return False, 'No such bone'
    if parent in branch(arm, name):
        return False, f'{parent} is under {name}'
    if int(ad.bones[name].get('n64_hidden_index', -1)) >= 0:
        return False, f'{name} is a hidden part: its parent is Bone > N64 Joint > Hidden part\'s'
    stamp(arm)
    sync(context, arm)                 # (pending edits first)
    if 'n64_joint_state' not in ad.keys() or 'n64_joint_parents' not in ad.keys():
        remember(arm)
    ad.bones[name]['n64_reparent_next'] = mode
    with busy():
        prev_mode, prev_active = arm.mode, context.view_layer.objects.active
        context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode='EDIT')
        eb = arm.data.edit_bones[name]
        eb.use_connect = False
        eb.parent = arm.data.edit_bones[parent]
        bpy.ops.object.mode_set(mode='OBJECT')
        if prev_mode == 'POSE':
            bpy.ops.object.mode_set(mode='POSE')
        context.view_layer.objects.active = prev_active
    msg = sync(context, arm)
    return True, msg or 'unchanged'
