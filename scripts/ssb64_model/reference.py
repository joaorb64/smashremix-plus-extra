"""Reference models for a fighter's .blend: common items in his hands and an opponent to throw.

    export_model.py (fighters) -> OUT_DIR/reference/ (build_blend.py -> blend_reference.py):
      items/<ITEM>/      Ray Gun, Beam Sword, Hammer (light: held at attr->joint_itemlight_id),
                         Crate, Barrel (heavy: attr->joint_itemheavy_id), from the ROM
      opponent/          Mario from the ROM with his grabbed / thrown animations
      reference.json     the hold joints (bone, rest world matrix) and what's where

In the game (decomp itMainSetFighterHold) a held item gets a root joint that copies the hold
joint's matrix every frame: the item model sits in that joint's frame. A grabbed / thrown
fighter (ftCommonCapturePulledRotateScale) is turned like the thrower's heavy item joint and
placed so that its TransN joint is at that joint (TopN = joint * -TransN.translate), playing its
own CapturePulled / Thrown* animation (which ones a throw uses: the thrower's attr->thrown_status
[victim kind]: forward / back throw).

Nothing here is imported back: it's only for looking at the fighter's item / throw animations.

The opponent is minimal: his high-detail model only, flat colors (each texture's average color),
the grabbed / thrown animations.
"""
import json
import os
import shutil
import struct
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROM = os.path.join(os.path.dirname(os.path.dirname(HERE)), 'smashremix', 'roms', 'original.z64')
A_ITEMHEAVY, A_ITEMLIGHT = 0x334, 0x33C            # FTAttributes joint_itemheavy_id / joint_itemlight_id
ITEMS = [('LGUN', 'Ray Gun'), ('SWORD', 'Beam Sword'), ('HAMMER', 'Hammer'), ('BOX', 'Crate'), ('TARU', 'Barrel')]
OPPONENT = 'MARIO'
OPPONENT_ANIMS = ('Capture', 'Thrown')            # the grabbed / thrown animations (names contain these)
JOINT_COMMON_START = 4                            # game joints 0..3: TopN, TransN, XRotN, YRotN


def bone_of(game_joint):
    return {1: 'TransN', 2: 'XRotN', 3: 'YRotN'}.get(game_joint, f'joint_{game_joint - JOINT_COMMON_START:02d}')


def hold_joints(f):
    """{'light' / 'heavy': dict(joint=game joint id, bone, world=4x4 rest matrix in the game's space)}."""
    out = {}
    J = f.details['hi']['joints']
    for key, off in (('light', A_ITEMLIGHT), ('heavy', A_ITEMHEAVY)):
        g = struct.unpack_from('>i', f.main, f.attr + off)[0]
        if g < JOINT_COMMON_START or g - JOINT_COMMON_START >= len(J):
            continue
        out[key] = dict(joint=g, bone=bone_of(g), world=np.array(J[g - JOINT_COMMON_START].world).tolist())
    return out


def fighter_size(f):
    """attr->size: the game scales the fighter's TopN by it (ftManagerMakeFighter), but not what it
    holds (a held item / grabbed fighter's frame is the hold joint's with the scale taken out)."""
    v = struct.unpack_from('>f', f.main, f.attr)[0]
    return v if 0.01 < v < 100 else 1.0


def export(f, out_dir, rom=DEFAULT_ROM, animations=True):
    """OUT_DIR/reference for fighter f (model_files): the items, the opponent, its hold joints."""
    if not os.path.exists(rom):
        print(f'  note: no reference models ({rom} not found)')
        return None
    ref = os.path.join(out_dir, 'reference')
    if os.path.exists(ref):
        shutil.rmtree(ref)
    models = build_models(rom, ref, animations)
    json.dump(dict(hold=hold_joints(f), size=fighter_size(f), **models), open(os.path.join(ref, 'reference.json'), 'w'), indent=1)
    return ref


def build_models(rom, folder, animations=True):
    """The items and the opponent from the ROM -> `folder` -> what's where."""
    import export_model as EX
    from SSB import N64, SSBtbl
    os.makedirs(folder, exist_ok=True)
    models = dict(items=[], opponent=None)
    for name, label in ITEMS:
        try:
            it = EX.load_rom_item(rom, name)
            weight_light = (struct.unpack_from('>I', it.item_attr, 0x10)[0] >> 27) & 1
            d = os.path.join(folder, 'items', name)
            ex = EX.Exporter(it, d, 1)
            ex.manifest['object'] = it.object
            ex.run()
            models['items'].append(dict(name=name, label=label, dir=os.path.relpath(d, folder),
                                        hold='light' if weight_light else 'heavy'))
        except (SystemExit, Exception) as e:                     # noqa: BLE001 (a reference: skip it)
            print(f'  note: reference item {name} not exported ({e})')
    try:
        entries = SSBtbl.fromROM(N64(open(rom, 'rb').read()))
        mid, cid = EX.FIGHTER_FILES[OPPONENT]
        me, ce = entries[mid], entries[cid]
        cd, cx = EX.rom_external_data(entries, ce)
        import model_files as MF
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, 'm'), 'wb').write(me.extract('data'))
            open(os.path.join(tmp, 'c'), 'wb').write(cd)
            op = MF.load(os.path.join(tmp, 'm'), os.path.join(tmp, 'c'), me.tbl or 0, me.res or 0, ce.tbl or 0, cx)
        # minimal: the high-detail body only (no low detail, model parts, electric skeleton, expressions)
        op.details = {'hi': op.details['hi']}
        op.modelparts, op.skeletons, op.textureparts = {}, {}, []
        d = os.path.join(folder, 'opponent')
        ex = EX.Exporter(op, d, 1)
        ex.fighter_anims = [a for a in EX.rom_animations(entries, op, OPPONENT)
                            if any(k in a['name'] for k in OPPONENT_ANIMS)] if animations else []
        ex.run()
        flatten_textures(d)
        models['opponent'] = dict(name=OPPONENT.title(), dir=os.path.relpath(d, folder), size=fighter_size(op),
                                  animations=[a['name'] for a in ex.fighter_anims])
    except (SystemExit, Exception) as e:                         # noqa: BLE001
        print(f'  note: reference opponent not exported ({e})')
    print(f"reference models: {', '.join(i['label'] for i in models['items'])}"
          + (f", opponent {models['opponent']['name']} ({len(models['opponent']['animations'])} animations)"
             if models['opponent'] else '') + f' -> {folder}')
    return models


def flatten_textures(folder):
    """An export's glTF without textures: each textured material gets its image's average color
    (alpha-weighted, linear) as its base color; textures/ is removed."""
    from PIL import Image
    path = os.path.join(folder, 'model.gltf')
    gl = json.load(open(path))
    images = gl.get('images') or []
    textures = gl.get('textures') or []
    for m in gl.get('materials', []):
        pbr = m.get('pbrMetallicRoughness') or {}
        tex = pbr.pop('baseColorTexture', None)
        if tex is None:
            continue
        uri = images[textures[tex['index']]['source']]['uri']
        px = np.asarray(Image.open(os.path.join(folder, uri)).convert('RGBA'), float).reshape(-1, 4) / 255
        w = px[:, 3:4]
        rgb = (px[:, :3] * w).sum(0) / max(w.sum(), 1e-6)
        lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        pbr['baseColorFactor'] = [float(x) for x in lin] + [1.0]
        m['pbrMetallicRoughness'] = pbr
        m.pop('alphaMode', None)
    for k in ('images', 'textures'):
        gl.pop(k, None)
    json.dump(gl, open(path, 'w'), indent=1)
    shutil.rmtree(os.path.join(folder, 'textures'), ignore_errors=True)
    # (the manifest's materials point at the PNGs: flat colors now)
    mp = os.path.join(folder, 'manifest.json')
    man = json.load(open(mp))
    for e in (man.get('materials') or {}).values():
        for k in ('costumes', 'images', 'palettes'):
            e.pop(k, None)
    json.dump(man, open(mp, 'w'), indent=1)
