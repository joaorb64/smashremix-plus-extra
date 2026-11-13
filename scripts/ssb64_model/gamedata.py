"""Game data that isn't the model, edited in Blender next to it: fighter hurtboxes and stage
collision (lines, map objects such as spawn / respawn points and item spawns, spawn
locations, blast zones, camera bounds).

Export: hurtboxes(...) / stage_collision(...) -> manifest entries (build_blend.py turns them
into objects, blend_gamedata.py). Import: blend_to_gltf.py writes gamedata.json (what the
.blend has); apply_hurtboxes / apply_stage write it where the build reads it: config.yaml
when it has the key (the build overwrites the files with it), else the .bin files.

Hurtboxes (decomp FTDamageCollDesc, FTAttributes + 0x104, 11 of them, 0x24 bytes):
{s32 joint_id, s32 placement (0 low, 1 mid, 2 high), sb32 is_grabbable, Vec3f offset,
Vec3f size}. A box is offset +- size / 2 in its joint's space: size is the full width (the
game halves it when the fighter loads, ftmanager.c; gmCollisionTestRectangle brings the hit
position into the joint's space). joint_id is the player joint
(DObjDesc index + 4); config.yaml's `bone` is the DObjDesc index. Order matters: the game
tests them in order and the first one hit gives the placement / grabbability. The import
writes them in the vanilla order (vanilla_order): torso (hip, chest), head, arms (upper pair,
lower pair), legs (upper pair, lower pair).
"""
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # appender root

import model_files as MF  # noqa: E402

HURTBOX_OFF = 0x104
HURTBOX_SIZE = 0x24
HURTBOX_MAX = 11
PLACEMENTS = ('low', 'mid', 'high')
BOUND_KEYS = ('blast_zones', 'camera_bounds', 'blast_zones_team', 'camera_bounds_team')
LINE_TYPES = ('floor', 'ceil', 'rwall', 'lwall')
MAPOBJ_REBIRTH = 0x20


# ---------------------------------------------------------------------------- config.yaml text

def _fmt(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float):
        return repr(round(v, 3))
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(_fmt(x) for x in v) + ']'
    if isinstance(v, dict):
        return '{ ' + ', '.join(f'{k}: {_fmt(x)}' for k, x in v.items()) + ' }'
    return str(v)


def yaml_block(text, path):
    """(start, end) line indices of key PATH ('a' or 'a.b') in config.yaml TEXT's lines, its
    value lines included; None when it isn't there."""
    lines = text.split('\n')
    lo, hi, indent = 0, len(lines), -1
    for depth, key in enumerate(path.split('.')):
        found = None
        for i in range(lo, hi):
            m = re.match(r'^(\s*)' + re.escape(key) + r'\s*:', lines[i])
            if m and len(m.group(1)) > indent and (depth == 0 and not m.group(1) or depth > 0):
                found, ind = i, len(m.group(1))
                break
        if found is None:
            return None
        end = found + 1
        while end < hi:
            s = lines[end]
            if s.strip() and len(s) - len(s.lstrip()) <= ind:
                break
            end += 1
        while end > found + 1 and not lines[end - 1].strip():      # (trailing blank lines stay)
            end -= 1
        lo, hi, indent = found, end, ind
    return lo, hi


def list_comments(text, path):
    """Comments around the items of the YAML list at PATH: [{before: [lines], trailing: str}]
    per item (lines relative to the key's indent), and the comment lines after the last item."""
    blk = yaml_block(text, path)
    if blk is None:
        return [], []
    lines = text.split('\n')
    s, e = blk
    ind = len(lines[s]) - len(lines[s].lstrip())
    items, pending, item_ind = [], [], None
    for ln in lines[s + 1:e]:
        st = ln.strip()
        m = re.match(r'^(\s*)- ', ln)
        if m and (item_ind is None or len(m.group(1)) == item_ind):
            item_ind = len(m.group(1))
            c = re.search(r'\s#\s?(.*)$', ln)
            items.append(dict(before=pending, trailing=c.group(1) if c else None))
            pending = []
        elif not st or st.startswith('#'):
            pending.append(ln[ind:] if ln[:ind].strip() == '' else ln)
    return items, pending


def with_comments(item_lines, comments, pad='  '):
    """Item lines (the first one starts with '- '), indented by PAD under the list's key, with an
    original item's comments (already relative to the key)."""
    items = [pad + ln for ln in item_lines]
    if not comments:
        return items
    if comments.get('trailing'):
        items[0] += f' # {comments["trailing"]}'
    return list(comments.get('before') or []) + items


def set_yaml(text, path, new_lines):
    """Replace key PATH's block in TEXT with NEW_LINES (its key line included, indented as the
    original), or append it at the end (top-level keys only)."""
    lines = text.split('\n')
    blk = yaml_block(text, path)
    if blk is None:
        if '.' in path:
            raise SystemExit(f'config.yaml: no {path} to update')
        while lines and not lines[-1].strip():
            lines.pop()
        return '\n'.join(lines + [''] + new_lines + [''])
    s, e = blk
    ind = lines[s][:len(lines[s]) - len(lines[s].lstrip())]
    return '\n'.join(lines[:s] + [ind + ln for ln in new_lines] + lines[e:])


# ---------------------------------------------------------------------------- hurtboxes

def read_hurtboxes(main, attr):
    out = []
    for i in range(HURTBOX_MAX):
        o = attr + HURTBOX_OFF + i * HURTBOX_SIZE
        j, p, g = struct.unpack_from('>iii', main, o)
        if j == -1:
            continue
        v = struct.unpack_from('>6f', main, o + 12)
        out.append(dict(joint=j - MF.JOINT_COMMON_START, placement=PLACEMENTS[p] if 0 <= p < 3 else p,
                        grabbable=bool(g), offset=list(v[:3]), size=list(v[3:])))
    return out


def config_hurtboxes(cfg):
    hb = (cfg.get('attributes') or {}).get('hurtboxes')
    if not hb:
        return None
    out = []
    for h in hb:
        p = h.get('height', 0)
        p = PLACEMENTS[p] if isinstance(p, int) else str(p).lower()
        out.append(dict(joint=int(h['bone']), placement=p, grabbable=bool(h.get('grabbable')),
                        offset=[float(x) for x in h['offset']], size=[float(x) for x in h['size']]))
    return out


def hurtboxes(f, cfg):
    """Manifest 'hurtboxes': {source, boxes: [{order, joint, placement, grabbable, offset, size,
    world (the joint's rest matrix, game space)}]}."""
    boxes, source = config_hurtboxes(cfg), 'config'
    if boxes is None:
        boxes, source = read_hurtboxes(f.main, f.attr), 'main'
    joints = f.details['hi']['joints']
    for k, b in enumerate(boxes):
        j = b['joint']
        b['order'] = k
        b['world'] = (joints[j].world if 0 <= j < len(joints) else np.eye(4)).tolist()
    return dict(source=source, boxes=boxes,       # (every joint's rest matrix: boxes added in Blender)
                joint_worlds={j.index: j.world.tolist() for j in joints})


def vanilla_order(boxes, joints):
    """Boxes sorted like vanilla fighters list them: torso (the joints the head hangs from, hip
    first), head (high), arms (other mid boxes), legs (low); upper joints first, then the
    pair's right one (higher joint index) before the left."""
    by_index = {j.index: j for j in joints}
    heads = [b['joint'] for b in boxes if b['placement'] == 'high']
    spine = set()
    for h in heads:
        j = by_index.get(h)
        while j is not None:
            spine.add(j.index)
            j = by_index.get(j.parent)

    def key(b):
        j = b['joint']
        d = by_index[j].depth if j in by_index else 0
        if b['placement'] == 'high':
            cat = 1
        elif b['placement'] == 'low':
            cat = 3
        elif j in spine or (not heads and b['grabbable']):
            cat = 0
        else:
            cat = 2
        return (cat, d, -j) if cat >= 2 else (cat, d, j)
    return sorted(boxes, key=key)


def apply_hurtboxes(f, main, cfg, work, man, cfg_path, write_cfg):
    """gamedata.json hurtboxes -> main (bytearray, in place) and config.yaml's list when it has
    one. Unchanged boxes keep their exact values. Returns a message or None."""
    gd = _load_work(work)
    if gd is None or 'hurtboxes' not in gd or 'hurtboxes' not in man:
        return None
    joints = f.details['hi']['joints']
    orig = {b['order']: b for b in man['hurtboxes']['boxes']}
    C_inv = np.linalg.inv(np.array(BLENDER_FROM_GAME))
    boxes = []
    for h in gd['hurtboxes']:
        j = h['joint']
        W = joints[j].world if 0 <= j < len(joints) else np.eye(4)
        L = np.linalg.inv(W) @ C_inv @ np.array(h['matrix'])
        axes = L[:3, :3]
        half = np.linalg.norm(axes, axis=0)
        rot = axes / np.where(half == 0, 1, half)
        size = 2 * half if h.get('half', True) else half    # (the full width: the game halves it;
                                                            # .blends built before showed it doubled)
        if np.abs(rot - np.eye(3)).max() > 0.02:
            print(f'  warning: hurtbox {h["name"]} is rotated against its joint (joint_{j:02d}); '
                  'hurtboxes are boxes along the joint\'s axes: its rotation is ignored')
        b = dict(joint=j, placement=h['placement'], grabbable=bool(h['grabbable']),
                 offset=[float(x) for x in L[:3, 3]], size=[float(x) for x in size], order=h.get('order'),
                 name=h['name'])
        o = orig.get(b['order'])
        for k in ('offset', 'size'):          # values not edited: exactly the original (no float noise)
            ref = o[k] if o and o['joint'] == j else [round(v) for v in b[k]]
            b[k] = [r if abs(r - v) < 0.01 else v for r, v in zip(ref, b[k])]
            b[k] = [0.0 if v == 0 else v for v in b[k]]
        boxes.append(b)
    if len(boxes) > HURTBOX_MAX:
        raise SystemExit(f'{len(boxes)} hurtboxes: the game has room for {HURTBOX_MAX}')

    def tup(b):
        return (b['joint'], b['placement'], b['grabbable'], b['offset'], b['size'])
    before = [tup(o) for o in sorted(orig.values(), key=lambda o: o['order'])]
    by_orig = sorted(boxes, key=lambda b: (b['order'] is None, b['order'] or 0))
    if [tup(b) for b in by_orig] == before:
        boxes = by_orig                       # nothing edited: as it was (a few fighters aren't vanilla-ordered)
    else:                                     # the order the game expects: torso, head, arms, legs
        boxes = vanilla_order(boxes, joints)
    after = [tup(b) for b in boxes]

    attr = f.attr
    for i in range(HURTBOX_MAX):
        o = attr + HURTBOX_OFF + i * HURTBOX_SIZE
        if i < len(boxes):
            b = boxes[i]
            struct.pack_into('>iii6f', main, o, b['joint'] + MF.JOINT_COMMON_START, PLACEMENTS.index(b['placement']),
                             int(b['grabbable']), *b['offset'], *b['size'])
        else:
            struct.pack_into('>iii6f', main, o, -1, 0, 0, 0, 0, 0, 0, 0, 0)
    if before == after:
        return f'hurtboxes: {len(boxes)}, unchanged'
    msg = f'hurtboxes: {len(boxes)} written to main.bin (' + ', '.join(
        f'joint_{b["joint"]:02d} {b["placement"]}' for b in boxes) + ')'
    if config_hurtboxes(cfg) is not None:
        lines = ['hurtboxes:']
        comments, tail = list_comments(open(cfg_path, encoding='utf-8').read(), 'attributes.hurtboxes') \
            if os.path.exists(cfg_path) else ([], [])
        for b in boxes:
            item = [f'- bone: {b["joint"]}', f'  height: {PLACEMENTS.index(b["placement"])}',
                    f'  grabbable: {int(b["grabbable"])}', f'  offset: {_fmt(b["offset"])}',
                    f'  size: {_fmt(b["size"])}']
            c = comments[b['order']] if b['order'] is not None and b['order'] < len(comments) else None
            lines += with_comments(item, c)
        lines += tail
        if write_cfg:
            text = open(cfg_path, encoding='utf-8').read()
            open(cfg_path, 'w', encoding='utf-8').write(set_yaml(text, 'attributes.hurtboxes', lines))
            msg += '; config.yaml attributes: hurtboxes updated'
        else:
            msg += '\n  config.yaml attributes: (not updated: --out)\n' + '\n'.join('  ' + ln for ln in lines)
    return msg


# ---------------------------------------------------------------------------- stage collision

# game (x, y, z) -> Blender (x, -z, y)
BLENDER_FROM_GAME = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]]


def _collision_mod():
    from smashremix_extra.stage import collision
    return collision


def _ground_mod():
    from smashremix_extra.stage import ground
    return ground


def stage_collision(stage_bytes, stage_head, header_bytes, ground_off, cfg):
    """Manifest 'stage_collision': lines (config.yaml `collision:` when it has one: the build
    writes it over stage.bin's), map objects (rebirth: config.yaml's when set), spawn
    locations and bounds (config.yaml's, else header.bin's), and which came from config.yaml."""
    col = _collision_mod()
    geo = col.decode(stage_bytes, stage_head)
    from_cfg = []
    if cfg.get('collision'):
        lines = []
        for e in cfg['collision']:
            t = next(t for t in LINE_TYPES if t in e)
            c = e[t]
            pf = [int(v) for v in e['point_flags']] if e.get('point_flags') else None
            lines.append(dict(group=int(e['group']), type=t, points=[[int(c[k]), int(c[k + 1])]
                                                                     for k in range(0, len(c), 2)],
                              flags=pf[0] if pf else col._flags(e, 'collision'), point_flags=pf))
        from_cfg.append('collision')
    else:
        lines = []
        for yid, t, _ln, pts in geo.iter_lines():
            mixed = len({p[2] for p in pts}) > 1
            lines.append(dict(group=yid, type=t, points=[[x, y] for x, y, _ in pts],
                              flags=pts[0][2] if pts else 0, point_flags=[p[2] for p in pts] if mixed else None))
    mapobjs = [dict(kind=k, name=col.MAPOBJ_KIND.get(k, f'kind 0x{k:02X}'), x=x, y=y) for k, x, y in geo.mapobjs]
    if cfg.get('rebirth'):
        for m in mapobjs:
            if m['kind'] == MAPOBJ_REBIRTH:
                m['x'], m['y'] = (int(v) for v in cfg['rebirth'])
        from_cfg.append('rebirth')
    bounds = {}
    if header_bytes is not None and ground_off is not None:
        g = _ground_mod().read(header_bytes, ground_off)
        bounds = {k: g[k] for k in BOUND_KEYS}
    for k in BOUND_KEYS:
        if cfg.get(k):
            bounds[k] = {s: int(v) for s, v in cfg[k].items()}
            from_cfg.append(k)
    spawns = {}
    for cat, pts in (cfg.get('spawn_locations') or {}).items():
        spawns[cat] = {p: [int(v) for v in xy] for p, xy in (pts or {}).items()}
    if spawns:
        from_cfg.append('spawn_locations')
    return dict(groups=geo.group_count, lines=lines, map_objects=mapobjs, bounds=bounds,
                spawn_locations=spawns, from_config=from_cfg)


def _load_work(work):
    import json
    p = os.path.join(work, 'gamedata.json')
    return json.load(open(p)) if os.path.exists(p) else None


def _line_entry(ln):
    col = _collision_mod()
    e = {'group': ln['group'], ln['type']: [c for p in ln['points'] for c in p]}
    names = col.flag_names(ln['flags'])
    if names:
        e['flags'] = names
    if ln.get('point_flags'):
        e['point_flags'] = ln['point_flags']
    return e


def apply_stage(work, man, stage_path, stage_head, header_path, ground_off, cfg_path, write_cfg):
    """gamedata.json collision / map objects / spawns / bounds -> config.yaml (keys it has) or
    stage.bin / header.bin (in place). Returns messages."""
    gd = _load_work(work)
    sc = man.get('stage_collision')
    if gd is None or 'stage_collision' not in gd or sc is None:
        return []
    new = gd['stage_collision']
    col = _collision_mod()
    msgs, cfg_edits = [], []
    from_cfg = set(sc['from_config'])

    # ---- collision lines
    def key(lines):
        return sorted((ln['group'], ln['type'], tuple(map(tuple, ln['points'])), ln['flags'],
                       tuple(ln.get('point_flags') or ())) for ln in lines)
    if key(new['lines']) != key(sc['lines']):
        groups = max((ln['group'] for ln in new['lines']), default=0)
        if groups > col._MAX_YAKUMONO_GROUPS:
            raise SystemExit(f'collision group {groups}: a stage has at most {col._MAX_YAKUMONO_GROUPS}')
        # the original order; a new line after the last one of its group
        kept = sorted((ln for ln in new['lines'] if ln.get('order') is not None), key=lambda ln: ln['order'])
        for ln in (ln for ln in new['lines'] if ln.get('order') is None):
            at = max((k + 1 for k, o in enumerate(kept) if o['group'] <= ln['group']), default=len(kept))
            kept.insert(at, ln)
        lines_sorted = kept
        if 'collision' in from_cfg:
            comments, tail = list_comments(open(cfg_path, encoding='utf-8').read(), 'collision')
            block = ['collision:']
            for ln in lines_sorted:
                o = ln.get('order')
                c = comments[o] if o is not None and o < len(comments) else None
                block += with_comments(['- ' + _fmt(_line_entry(ln))], c)
            block += tail
            cfg_edits.append(('collision', block))
        else:
            info = col.apply(stage_path, [_line_entry(ln) for ln in lines_sorted], stage_head,
                             header_path=header_path, groupdata_off=ground_off)
            msgs.append(f'collision: {info["lines"]} lines, {info["vertices"]} vertices, '
                        f'{info["groups"]} groups -> stage.bin')
    else:
        msgs.append(f'collision: {len(new["lines"])} lines, unchanged')

    # ---- map objects (spawn points, item spawns, rebirth platform...)
    old_mo = [(m['kind'], m['x'], m['y']) for m in sc['map_objects']]
    new_mo = [(m['kind'], m['x'], m['y']) for m in new['map_objects']]
    if new_mo != old_mo:
        rebirth = [m for m in new_mo if m[0] == MAPOBJ_REBIRTH]
        if 'rebirth' in from_cfg and rebirth:
            cfg_edits.append(('rebirth', [f'rebirth: [{rebirth[0][1]}, {rebirth[0][2]}]']))
        to_bin = new_mo
        if 'rebirth' in from_cfg:                 # (the build sets the rebirth platform from config.yaml)
            to_bin = [m if m[0] != MAPOBJ_REBIRTH else
                      next((o for o in old_mo if o[0] == MAPOBJ_REBIRTH), m) for m in new_mo]
        write_mapobjs(stage_path, stage_head, to_bin)
        msgs.append(f'map objects: {len(new_mo)} -> stage.bin')

    # ---- spawn locations (config.yaml only: the build writes them into the stage table)
    if new.get('spawn_locations') and new['spawn_locations'] != sc.get('spawn_locations'):
        block = ['spawn_locations:']
        for cat, pts in new['spawn_locations'].items():
            block.append(f'  {cat}:')
            block += [f'    {p}: [{xy[0]}, {xy[1]}]' for p, xy in sorted(pts.items())]
        cfg_edits.append(('spawn_locations', block))

    # ---- blast zones / camera bounds
    hdr_changed = []
    for k in BOUND_KEYS:
        nb, ob = new['bounds'].get(k), sc['bounds'].get(k)
        if nb is None or nb == ob:
            continue
        if k in from_cfg:
            cfg_edits.append((k, [f'{k}: ' + _fmt({s: nb[s] for s in ('top', 'bottom', 'left', 'right')})]))
        else:
            hdr_changed.append(k)
    if hdr_changed:
        _ground_mod().apply(header_path, ground_off, {k: new['bounds'][k] for k in hdr_changed})
        msgs.append(f'{", ".join(hdr_changed)} -> header.bin')

    if cfg_edits:
        if write_cfg:
            text = open(cfg_path, encoding='utf-8').read()
            for k, block in cfg_edits:
                text = set_yaml(text, k, block)
            open(cfg_path, 'w', encoding='utf-8').write(text)
            msgs.append('config.yaml: ' + ', '.join(k for k, _ in cfg_edits) + ' updated')
        else:
            msgs.append('config.yaml (not updated: --out):\n' + '\n'.join(
                '  ' + ln for _, block in cfg_edits for ln in block))
    return msgs


def write_mapobjs(stage_path, stage_head, mapobjs):
    """MPGeometryData's map objects: rewritten in place (same count), else a new array appended
    and pointed at (its chain node kept)."""
    col = _collision_mod()
    d = bytearray(open(stage_path, 'rb').read())
    geom = col._find_geometry(d, col._walk_chain(d, stage_head))
    if geom is None:
        raise SystemExit('could not locate MPGeometryData in stage.bin')
    cnt = col._u16(d, geom + 0x14)
    blob = b''.join(struct.pack('>Hhh', k, max(-32768, min(32767, int(x))), max(-32768, min(32767, int(y))))
                    for k, x, y in mapobjs)
    if len(mapobjs) == cnt:
        p = (col._u32(d, geom + 0x18) & 0xFFFF) * 4
        d[p:p + len(blob)] = blob
    else:
        d += b'\0' * (-len(d) % 4)
        target = len(d)
        d += blob
        struct.pack_into('>H', d, geom + 0x14, len(mapobjs))
        struct.pack_into('>H', d, geom + 0x18 + 2, target // 4)
    open(stage_path, 'wb').write(d)
