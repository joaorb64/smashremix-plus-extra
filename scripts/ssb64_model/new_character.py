"""A new character folder from a vanilla fighter (ssb64_model.py new BASE FOLDER): the starting
point for a character made in Blender.

Writes FOLDER/main.bin + character.bin (the base fighter's, from the original ROM), their
reqlists (the base's own model file -> ${CHARACTER}, other files by vanilla ID: moveset, shield
pose, effects...), config.yaml (offsets, base_character, costumes counted from the costume
scripts, files 6..9 as the base has them) and a main.asm with an empty scope: every action plays
the base's animations and moves. Then export FOLDER, edit, import.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.path.normpath(os.path.join(HERE, '..', '..')))

import motions as MO  # noqa: E402

# the build's base_character names (smashremix_extra.constants PRIMARY_MOVESETS / SHIELD_POSES)
BASE_NAMES = {0: 'MARIO', 1: 'FOX', 2: 'DONKEY', 3: 'SAMUS', 4: 'LUIGI', 5: 'LINK', 6: 'YOSHI', 7: 'CAPTAIN',
              8: 'KIRBY', 9: 'PIKACHU', 10: 'JIGGLYPUFF', 11: 'NESS'}
FTDATA_FILES = 9        # FTData: main, mainmotion (moveset), submotion, model, shieldpose, special1..4


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('base', help='vanilla fighter: MARIO, FOX, DK, SAMUS, LUIGI, LINK, YOSHI, CAPTAIN (FALCON), '
                                 'KIRBY, PIKACHU, JIGGLYPUFF, NESS')
    ap.add_argument('folder', help='the new character folder (extra_characters/<Name>); its name is the '
                                   'character\'s id in the build')
    ap.add_argument('--name', help='display name (default: the folder name)')
    ap.add_argument('--force', action='store_true', help='write into an existing folder')
    a = ap.parse_args(argv)
    cid = MO.CHAR_IDS.get(a.base.upper())
    if cid is None:
        raise SystemExit(f'{a.base}: not a vanilla fighter ({", ".join(BASE_NAMES.values())})')
    base = BASE_NAMES[cid]
    folder = a.folder.rstrip('/\\')
    name = os.path.basename(folder)
    if not name.isidentifier():
        raise SystemExit(f'{name}: the folder name becomes ASM identifiers (File.{name.upper()}_MAIN...): '
                         'letters, digits and _ only')
    if os.path.exists(os.path.join(folder, 'config.yaml')) and not a.force:
        raise SystemExit(f'{folder} already has a config.yaml (--force to overwrite)')
    os.makedirs(folder, exist_ok=True)

    from SSB import SSBtbl, N64
    van = MO.Vanilla()
    ids = struct.unpack_from(f'>{FTDATA_FILES}I', van.rom, van.struct(cid))
    main_id, moveset_id, _, char_id, shield_id = ids[:5]
    specials = ids[5:9]
    rom = SSBtbl.fromROM(N64(open(MO.ORIGINAL_ROM, 'rb').read()))

    def write(fid, fn):
        e = rom[fid]
        open(os.path.join(folder, fn + '.bin'), 'wb').write(e.extract('data'))
        idx = e.extract('idx')
        req = [int.from_bytes(idx[k:k + 2], 'big') for k in range(0, len(idx), 2)]
        if req:
            label = {moveset_id: f'{base} moveset', shield_id: f'{base} shield pose'}
            with open(os.path.join(folder, fn + '_reqlist.txt'), 'w', encoding='utf-8') as f:
                for x in req:
                    f.write('${CHARACTER}\n' if x == char_id else f'{x:04X}' + (f' {label[x]}' if x in label else '') + '\n')
                f.write('END OF REQ LIST')
        return e
    em = write(main_id, 'main')
    ec = write(char_id, 'character')

    import export_model as EX
    import model_files as MF
    cfg_char = os.path.join(folder, 'character.bin')
    data, ext, _ = EX.folder_external_data(
        {'offsets': {'character': ['%04X' % ec.tbl, '%04X' % (ec.res if ec.res is not None else 0x3FFFC)]}},
        folder, open(cfg_char, 'rb').read())
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 'character.bin')
        open(p, 'wb').write(data)
        f = MF.load(os.path.join(folder, 'main.bin'), p, em.tbl, em.res if em.res is not None else 0x3FFFC,
                    ec.tbl, ext)
        costumes = EX.costumes_in_scripts(f) or 1
    files = ', '.join(f'"0x{x:X}"' for x in specials)
    cfg = f"""# New character based on {base} (ssb64_model.py new): export this folder, edit it in Blender, import
# [InternalFileTableOffsetBytes, InternalFileResourceOffsetBytes]
offsets:
  main: ["{em.tbl:04X}", "{em.res if em.res is not None else 0x3FFFC:04X}"]
  character: ["{ec.tbl:04X}", "{ec.res if ec.res is not None else 0x3FFFC:04X}"]

definitions:
  base_character: {base}
  select_pose: 4
  variant_type: NA

name: "{a.name or name}"
num_costumes: {costumes}

results:
  name: "{(a.name or name).upper()}"

# Files 6, 7, 8, 9 ({base}'s)
files: [{files}]
"""
    open(os.path.join(folder, 'config.yaml'), 'w', encoding='utf-8').write(cfg)
    if not os.path.exists(os.path.join(folder, 'main.asm')) or a.force:
        open(os.path.join(folder, 'main.asm'), 'w', encoding='utf-8').write(
            f'// {name}: {base}\'s moves and animations until edited (ssb64_model import writes its own here)\n'
            f'scope {name} {{\n}}\n')
    print(f'{folder}: {base} (main 0x{main_id:X}, model 0x{char_id:X}, moveset 0x{moveset_id:X}, shield pose '
          f'0x{shield_id:X}), {costumes} costumes')
    print(f'next: ssb64_model.py export {folder} OUT_DIR')


if __name__ == '__main__':
    main()
