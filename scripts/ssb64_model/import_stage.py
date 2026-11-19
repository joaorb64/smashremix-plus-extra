#!/usr/bin/env python3
"""Rebuild a stage's stage.bin geometry layers from an edited .blend (export_model.py --stage).

    # (ssb64_model.py import OUT_DIR does both steps)
    # 1. inside Blender: one glTF per geometry layer (layer0.gltf, layer1.gltf...)
    blender -b model.blend --python blend_to_gltf.py -- WORK_DIR
    # 2. write stage.bin (and re-point header.bin)
    python3 import_stage.py STAGE_DIR WORK_DIR [--out OUT_DIR]

stage.bin is written from scratch: the rebuilt layers, plus everything else header.bin
points at in it (collision geometry, layer animations, ...), so leftover data from the
original export is gone. Each layer keeps its DObjDesc array (transforms, order: the
collision groups and hazard code address its joints by index). header.bin's pointers are
updated, and the stage chain head in config.yaml (offsets: stage).
"""
import argparse
import json
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # appender root
import model_files as MF  # noqa: E402
import gbi_writer as GW  # noqa: E402
import import_fighter as IF  # noqa: E402
import export_model as EX  # noqa: E402
import import_object as IO  # noqa: E402
from smashremix_extra import ge_bin as ge  # noqa: E402

# MPGroundDesc: {DObjDesc*, anim_joints, p_mobjsubs, p_matanim_joints} (anim_joints is kept)
LAYER_FIELDS = {IO.DATA: 0x0, IO.MOBJSUBS: 0x8, IO.MATANIM: 0xC}


def set_stage_head(cfg_path, head):
    text = open(cfg_path, encoding='utf-8').read()
    pat = re.compile(r'(\n\s*stage:\s*\[\s*")([0-9A-Fa-f]+)(")')
    if not pat.search(text):
        raise SystemExit(f'{cfg_path}: no offsets: stage: ["HEAD", ...] to update')
    open(cfg_path, 'w', encoding='utf-8').write(pat.sub(lambda m: m.group(1) + f'{head:04X}' + m.group(3), text, 1))


def add_new_joints(f, layer_objs, work_dir):
    """New joints: a layer's meshes skinned to (or animations on, or new bones) L<n>_joint_NN past its
    last joint add joints up to NN, with no rest transform (identity), children of the layer's root or
    of their parent bone's joint (blend_to_gltf new_joints), drawn after the original ones (joints draw
    in index order). Their DObjDescs are appended (ModelWriter.build)."""
    tab = json.load(open(os.path.join(work_dir, 'costumes.json'))) \
        if os.path.exists(os.path.join(work_dir, 'costumes.json')) else {}
    anims, parents = tab.get('joint_anims') or {}, tab.get('new_joints') or {}
    for name, objs in layer_objs.items():
        d = f.details[name]
        js = d['joints']
        if d.get('dobjdesc') is None or not js:
            continue
        used = [int(np.max(o['joint'])) for o in objs if len(o['joint'])]
        used += [int(k) for k in (anims.get(name) or {})] + [int(k) for k in (parents.get(name) or {})]
        want = max(used, default=-1)
        if want < len(js):
            continue
        d['orig_joints'] = len(js)
        par = {int(k): int(v) for k, v in (parents.get(name) or {}).items()}
        while len(js) <= want:
            i = len(js)
            p = par.get(i, 0)
            # DObjDescs are depth-first: a joint's parent must be the joint before it or one of
            # that joint's ancestors
            chain, a = [], i - 1
            while a is not None:
                chain.append(a)
                a = js[a].parent
            if p not in chain:
                raise SystemExit(f'{name} joint {i}: its parent {p} must be joint {i - 1} or one of its '
                                 f'ancestors {chain[1:]} (joints are stored depth first)')
            j = MF.Joint(i, js[p].depth + 1, p, None, np.zeros(3), np.zeros(3), np.ones(3))
            j.world = js[p].world.copy()
            js.append(j)
        print(f'  layer {name[1:]}: {len(js) - d["orig_joints"]} new joints ({d["orig_joints"]}..{len(js) - 1})')


def apply_joint_map(f, jmap):
    """The .blend's joints (blend_to_gltf joint_map: every layer's {index, orig, parent}): each layer's joint
    list rebuilt in its order. A joint keeps its original's transform, DObjDesc flags, display-list form,
    animation script and MObj order (remapped to its new index); re-parented, it keeps its place in the
    world (its transform recomputed from the new parent); new joints have none. Returns the layers it
    removed (no joints left). Unchanged layers stay exactly as they were."""
    import jointanim as JA
    from smashremix_extra.stage import collision as COL
    removed = []
    for name in list(f.details):
        d = f.details[name]
        rows = jmap.get(name)
        if rows is None or d.get('dobjdesc') is None:
            continue
        if not rows:
            removed.append(name)
            continue
        old = d['joints']
        if [r['orig'] for r in rows] == list(range(len(old))) and \
                all(r['parent'] == old[i].parent for i, r in enumerate(rows)):
            continue
        if [r['index'] for r in rows] != list(range(len(rows))):
            raise SystemExit(f'{name}: joints {[r["index"] for r in rows]}: they must be numbered 0..{len(rows) - 1}')
        if name == 'L1':                 # collision groups (yakumono) move with layer 1's joints 1..N
            try:
                groups = COL.decode(bytes(f.char)).group_count
            except Exception:            # noqa: BLE001  (no collision found: nothing to keep)
                groups = 0
            for i in range(min(groups + 1, len(old))):
                if i >= len(rows) or rows[i]['orig'] != i:
                    raise SystemExit(f'{name} joint {i}: collision group {i} moves with it: layer 1\'s joints '
                                     f'0..{groups} must stay where they are')
        js = []
        for r in rows:
            i, o, p = r['index'], r['orig'], r['parent']
            if i == 0:
                if p is not None or o != 0:
                    raise SystemExit(f'{name}: joint 0 is the layer\'s root: it can\'t move')
            else:                        # depth first: the parent is the joint before or one of its ancestors
                chain, a = [], i - 1
                while a is not None:
                    chain.append(a)
                    a = js[a].parent
                if p not in chain:
                    raise SystemExit(f'{name} joint {i}: its parent {p} must be joint {i - 1} or one of its '
                                     f'ancestors {chain[1:]} (joints are stored depth first)')
            par = js[p] if p is not None else None
            depth = par.depth + 1 if par is not None else 0
            if o is not None:
                src = old[o]
                same = (p is None and src.parent is None) or (p is not None and rows[p]['orig'] == src.parent)
                if same:
                    t, rr, sc = src.t, src.r, src.s
                else:                    # its world place kept: its transform from the new parent
                    v = JA.decompose(np.linalg.inv(par.world) @ src.world)
                    t = np.array([v['tx'], v['ty'], v['tz']])
                    rr = np.array([v['rx'], v['ry'], v['rz']])
                    sc = np.array([v['sx'], v['sy'], v['sz']])
                j = MF.Joint(i, depth, p, src.dl, t, rr, sc)
                j.world = src.world.copy()
            else:
                same = False
                j = MF.Joint(i, depth, p, None, np.zeros(3), np.zeros(3), np.ones(3))
                j.world = par.world.copy()
            j.orig, j.keep = o, same
            js.append(j)
        d['orig_count'] = len(old)
        d['joints'] = js
        d['joint_orig'] = [j.orig for j in js]
        moved = sum(1 for j in js if j.orig is not None and j.orig != j.index)
        print(f'  layer {name[1:]}: {len(old)} -> {len(js)} joints ({sum(1 for j in js if j.orig is None)} new, '
              f'{len(old) - sum(1 for j in js if j.orig is not None)} removed, {moved} renumbered)')
    return removed


def remove_layers(f, header, ext_head, req, removed):
    """Layers left without joints: their MPGroundDesc fields (DObjDescs, animation, MObjs, material
    animation) emptied, their external pointer nodes unlinked from the header's chain and their
    header_reqlist.txt lines dropped. -> how many lines went."""
    gone = 0
    for name in removed:
        d = f.details.pop(name)
        if name == 'L0':
            raise SystemExit('layer 0 holds the header\'s first external pointer: it can\'t be removed '
                             '(leave its root joint)')
        for rel in (0x0, 0x4, 0x8, 0xC):
            fld = d['field'] + rel
            order = list(MF.chain(bytes(header), ext_head))
            if fld in order:
                if fld == ext_head:
                    raise SystemExit(f'{name}: its field 0x{fld:X} heads the header\'s chain: it can\'t be removed')
                k = order.index(fld)
                nxt = struct.unpack_from('>H', header, fld)[0]
                struct.pack_into('>H', header, order[k - 1], nxt)            # the previous node skips it
                del req[k]
                gone += 1
            struct.pack_into('>I', header, fld, 0)
        print(f'  layer {name[1:]}: removed (no joints left)')
    return gone


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage_dir')
    ap.add_argument('work_dir', help='blend_to_gltf.py output (layer<N>.gltf)')
    ap.add_argument('--out', help='write the files here (default: the stage folder, updating config.yaml)')
    ap.add_argument('--manifest', help="the export's manifest.json (default: the one the .blend was built from)")
    a = ap.parse_args(argv)

    cfg_path = os.path.join(a.stage_dir, 'config.yaml')
    kw = EX.stage_files(EX.read_config(a.stage_dir), a.stage_dir)
    f = MF.load_stage(**kw)
    header = bytearray(f.main)
    ext_head = kw['header_ext_head']
    ext = MF.chain(bytes(header), ext_head)
    ext_fields = list(ext)

    reqlist_path = os.path.join(a.stage_dir, 'header_reqlist.txt')
    req_raw = open(reqlist_path, 'rb').read()
    newline = '\r\n' if b'\r\n' in req_raw else '\n'
    req = req_raw.decode('utf-8').splitlines()
    end = next((i for i, ln in enumerate(req) if ln.startswith('END OF')), len(req))
    # the .blend's joints: renumbered / removed / new ones, layers left empty go
    costumes = os.path.join(a.work_dir, 'costumes.json')
    jmap = json.load(open(costumes)).get('joint_map') if os.path.exists(costumes) else None
    lines_gone = 0
    if jmap:
        lines_gone = remove_layers(f, header, ext_head, req, apply_joint_map(f, jmap))
        if lines_gone:
            ext = MF.chain(bytes(header), ext_head)
            ext_fields = list(ext)
            end = next((i for i, ln in enumerate(req) if ln.startswith('END OF')), len(req))
    if end < len(ext_fields):        # (more lines: files the stage loads without a pointer, e.g. items)
        raise SystemExit(f'{reqlist_path}: {end} lines but {len(ext_fields)} external pointers')
    first_layer = f.details[next(iter(f.details))]['field']
    token = req[ext_fields.index(first_layer)].strip()               # stage.bin's ${...}
    into_stage = [fld for fld, ln in zip(ext_fields, req) if ln.strip() == token]

    # rebuilt from the .blend: each layer's model fields; kept: everything else in stage.bin
    model_fields = {}                                      # header field -> (layer, role)
    layer_objs = {}
    for name, d in f.details.items():
        path = os.path.join(a.work_dir, f'layer{name[1:]}.gltf')
        if os.path.exists(path):
            layer_objs[name] = IF.load_gltf(path)
        elif not os.path.exists(os.path.join(a.work_dir, 'costumes.json')):
            raise SystemExit(f'{a.work_dir}: not a blend_to_gltf.py output')
        else:              # no meshes in that layer (its joints draw nothing): DObjDescs only
            print(f'  layer {name[1:]}: no meshes in the .blend (its joints get no display lists)')
            layer_objs[name] = []
        for role, rel in LAYER_FIELDS.items():
            model_fields[d['field'] + rel] = (name, role)
    if not jmap:                                   # (older blend_to_gltf.py: new joints only)
        add_new_joints(f, layer_objs, a.work_dir)
    keep = [ext[fld] for fld in into_stage if fld not in model_fields]
    scripts = IO.material_scripts(f)               # material animations (water...): kept as they are
    if keep or scripts:
        res = ge.gc(f.char, keep + sorted(set(scripts.values())), head=kw['stage_int_head'])
        base, base_head, remap = bytearray(res.data), res.new_head, res.remap
    else:
        base, base_head, remap = bytearray(), None, {}
    base += b'\0' * (-len(base) % 8)
    blob = GW.Blob(len(base))

    textures = IF.Materials(a.work_dir, 1, f.char, a.manifest)
    textures.cloak_safe = False                       # stages keep their own render modes
    writer = IO.ModelWriter(f, textures, blob,      # (materials shared by layers stored once)
                            {ms: remap[sc] for ms, sc in scripts.items()})
    targets, tris = {}, {}
    for name, objs in layer_objs.items():
        d = f.details[name]
        need = [role for role, rel in LAYER_FIELDS.items() if d['field'] + rel in ext]
        t, dls = writer.build(name, objs, need)
        for role, tgt in t.items():
            targets[d['field'] + LAYER_FIELDS[role]] = tgt
        tris[name] = (sum(len(o['tris']) for o in objs), sorted(dls))

    # ---- joint animations (anim_joints): Blender's, per joint (blend_to_gltf.py joint_anims)
    anim_targets = IO.write_joint_anims(f, a.work_dir, blob, remap)

    if base_head is None:
        new, head = IO.standalone(blob)
    else:
        new = bytes(GW.append_blob(base, blob, base_head, lambda d, h: MF.chain(d, h)))
        head = base_head
    if len(new) > 0x3FFFC:
        raise SystemExit(f'stage.bin is {len(new)} bytes: over the 0x3FFFC limit')

    # ---- re-point header.bin (kept: remapped; layer models: the new data)
    for fld in into_stage:
        if fld in anim_targets:
            tgt = anim_targets[fld]
        elif fld in model_fields:
            if fld not in targets:
                name, role = model_fields[fld]
                raise SystemExit(f'{name}: header 0x{fld:X} pointed into stage.bin but the new layer has no such data')
            tgt = targets[fld]
        else:
            tgt = remap[ext[fld]]
        struct.pack_into('>H', header, fld + 2, tgt // 4)
    added = 0
    targets.update(anim_targets)
    for fld, tgt in sorted(targets.items()):
        if fld in ext:
            continue
        if MF.u32(header, fld) != 0:
            raise SystemExit(f'header 0x{fld:X} holds data, not an (empty) pointer field')
        tail = list(MF.chain(bytes(header), ext_head))[-1]
        struct.pack_into('>H', header, tail, fld // 4)
        struct.pack_into('>HH', header, fld, GW.NULL, tgt // 4)
        req.insert(len(MF.chain(bytes(header), ext_head)) - 1, token)
        added += 1

    out = a.out or a.stage_dir
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'stage.bin'), 'wb').write(new)
    open(os.path.join(out, 'header.bin'), 'wb').write(header)
    if added or lines_gone:
        open(os.path.join(out, 'header_reqlist.txt'), 'w', encoding='utf-8', newline='').write(
            newline.join(req) + (newline if req_raw.endswith(b'\n') else ''))
    if a.out is None:
        set_stage_head(cfg_path, head)
    # collision lines, map objects, spawn locations, bounds (the .blend's Collision / Stage objects)
    import gamedata as GD
    for msg in GD.apply_stage(a.work_dir, getattr(textures, 'manifest', None) or {}, os.path.join(out, 'stage.bin'),
                              head, os.path.join(out, 'header.bin'), f.object['ground'], cfg_path,
                              write_cfg=a.out is None):
        print('  ' + msg)
    print(f'stage.bin: {len(f.char)} -> {len(new)} bytes, chain head 0x{head:X}; kept {len(keep)} other '
          f'header targets (collision, animations...)' + (f'; {added} new header pointers' if added else ''))
    for name, (n, js) in tris.items():
        print(f'  layer {name[1:]}: {n} triangles on joints {js}')
    if a.out:
        print(f'  (config.yaml not updated: set offsets: stage to "{head:04X}")')


if __name__ == '__main__':
    main()
