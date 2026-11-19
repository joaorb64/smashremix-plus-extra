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
    open(os.path.join(ref, 'reference.json'), 'w').write(json.dumps(dict(hold=hold_joints(f), size=fighter_size(f), **models), indent=1))
    return ref


def build_models(rom, folder, animations=True):
    """The items and the opponent from the ROM -> `folder` -> what's where."""
    import export_model as EX
    from SSB import N64, SSBtbl
    os.makedirs(folder, exist_ok=True)
    models = dict(items=[], opponent=None)
    entries = None
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
        ex.run()                                                 # (textured: the N64 look in Blender)
        models['opponent'] = dict(name=OPPONENT.title(), dir=os.path.relpath(d, folder), size=fighter_size(op),
                                  animations=[a['name'] for a in ex.fighter_anims])
    except (SystemExit, Exception) as e:                         # noqa: BLE001
        print(f'  note: reference opponent not exported ({e})')
    try:
        models['shield_graphic'] = shield_graphic(entries or SSBtbl.fromROM(N64(open(rom, 'rb').read())), folder)
    except (SystemExit, Exception) as e:                         # noqa: BLE001
        print(f'  note: shield graphic not exported ({e})')
    print(f"reference models: {', '.join(i['label'] for i in models['items'])}"
          + (f", opponent {models['opponent']['name']} ({len(models['opponent']['animations'])} animations)"
             if models['opponent'] else '') + f' -> {folder}')
    return models


SHIELD_FILE = 163          # FTManagerCommon: the shield effect's model (decomp efManagerShieldMakeEffect)
SHIELD_TEX, SHIELD_VTX = 0x8, 0x208
# efManagerShieldProcDisplay: prim / env per port (index 4: the frame the shield is hit), alpha 0xC0
SHIELD_COLORS = [((255, 255, 255), (255, 0, 0)), ((255, 255, 255), (0, 255, 0)), ((255, 255, 255), (0, 0, 255)),
                 ((255, 255, 255), (0, 0, 0)), ((255, 255, 255), (192, 192, 192))]


def shield_graphic(entries, folder):
    """The shield bubble as the game draws it -> folder/shield.png + its draw parameters.

    A camera-facing quad (matrix kinds 0x4F: YRotN's world position, scale = YRotN's world
    scale; 0x2C: billboard) of +-30 units with a 16x32 IA8 half disc, mirrored in S (tile 32x32,
    mirror + clamp), combiner color = lerp(env, prim, texel), alpha = texel alpha * prim alpha.
    ftCommonGuardUpdateShieldCollision scales YRotN by (0.65 * health / 55 + 0.35) * shield_size / 30:
    at full health the quad's half width is shield_size (in the fighter's unscaled space)."""
    import model_files as MF
    from PIL import Image
    d = entries[SHIELD_FILE].extract('data')
    half = max(abs(struct.unpack_from('>h', d, SHIELD_VTX + 16 * k)[0]) for k in range(4))
    tex = MF.decode_texture(d, SHIELD_TEX, 3, 1, 16, 32, None)          # IA8 16x32
    disc = np.concatenate([tex, tex[:, ::-1]], axis=1)                   # mirror in S -> 32x32
    # vertex t = 32 at the quad's top (+y): texture rows run bottom -> top; the PNG is upright
    Image.fromarray(np.ascontiguousarray(disc[::-1]), 'RGBA').save(os.path.join(folder, 'shield.png'))
    return dict(texture='shield.png', half=half, prim_alpha=0xC0, health_max=55, scale_min=0.35,
                colors=[dict(prim=p, env=e) for p, e in SHIELD_COLORS],
                ports=['P1', 'P2', 'P3', 'P4 / CPU', 'Hit'])
