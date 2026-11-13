#!/usr/bin/env python3
"""Edit Smash 64 models in Blender: fighters, items / projectiles and stages.

    ssb64_model.py export SOURCE [NAME] OUT_DIR     game files -> OUT_DIR/model.blend
    ssb64_model.py import OUT_DIR [TARGET]           edited model.blend -> game files
    ssb64_model.py blend OUT_DIR                     (re)build only the .blend of an export
    ssb64_model.py preview OUT_DIR                   (re)render only OUT_DIR/previews/

SOURCE is a folder or a ROM:
  extra_characters/<Name>            the fighter (main.bin + character.bin)
  extra_stages/<Name>                the stage's geometry layers (header.bin + stage.bin)
  <folder> NAME                      the folder's item / projectile NAME (config.yaml NAME_hitbox + NAME_gfx / NAME)
  ROM.z64 NAME                       a fighter (MARIO, FALCO...), stage (DREAMLAND, BATTLEFIELD, or the header
                                     file's hex ID) or common item (HAMMER...) by name: --kind if a name is ambiguous

export runs Blender to build the .blend (--no-blend: only the glTF / textures / manifest), import runs
Blender to read it back. The import writes to the folder the export came from (recorded in
manifest.json); give TARGET for a ROM export or another folder, or --out to write elsewhere.
Arguments after `--` go to export_model.py / import_<kind>.py as they are (e.g. -- --attr-offset 10).

Blender: --blender CMD, else $BLENDER, else `blender` on the PATH, else the Flatpak
(org.blender.Blender: it can't see /tmp, keep the export folder elsewhere).
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

KINDS = ('fighter', 'stage', 'object')
BLEND_NAMES = ('model.blend', 'model.blend')      # (model.blend: exports made before the rename)


# ---------------------------------------------------------------------------- Blender

def blender_cmd(opt=None):
    for cmd in (opt, os.environ.get('BLENDER')):
        if cmd:
            return shlex.split(cmd)
    if shutil.which('blender'):
        return ['blender']
    if shutil.which('flatpak') and subprocess.run(['flatpak', 'info', 'org.blender.Blender'],
                                                  capture_output=True).returncode == 0:
        return ['flatpak', 'run', 'org.blender.Blender']
    raise SystemExit('Blender not found: install it, or pass --blender CMD (or set $BLENDER). '
                     '--no-blend skips the Blender step.')


def run_blender(cmd, script, folder, blend=None):
    """Run SCRIPT in background Blender: `blender -b [BLEND] --python SCRIPT -- FOLDER`."""
    folder = os.path.abspath(folder)
    if 'flatpak' in cmd[0] and os.path.realpath(folder).startswith('/tmp/'):
        print(f'  warning: Flatpak Blender can\'t see /tmp ({folder}): use a folder elsewhere')
    argv = cmd + ['-b', '--factory-startup'] + ([os.path.abspath(blend)] if blend else []) + \
        ['--python-exit-code', '1', '--python', os.path.join(HERE, script), '--', folder]
    print('$', ' '.join(shlex.quote(x) for x in argv))
    if subprocess.run(argv).returncode:
        raise SystemExit(f'Blender failed running {script}')


def find_blend(out_dir):
    for name in BLEND_NAMES:
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            return p
    raise SystemExit(f'no model.blend in {out_dir} (export it first, or --blend FILE)')


# ---------------------------------------------------------------------------- export

def rom_kind(name):
    """The kinds a ROM name can be (export_model.py's name tables)."""
    import export_model as EX
    key = name.upper()
    bare = key[4:] if key.startswith('STG_') else key
    bare = bare[:-7] if bare.endswith('_HEADER') else bare
    kinds = []
    if key in EX.FIGHTER_FILES:
        kinds.append('fighter')
    stages = EX.remix_stages()
    if key in EX.VANILLA_STAGES or key in stages or bare in stages:
        kinds.append('stage')
    if key in EX.VANILLA_ITEMS:
        kinds.append('object')
    if not kinds:
        try:
            int(name, 16)
            kinds.append('stage')                       # a stage header file's hex ID
        except ValueError:
            pass
    return kinds


def export_argv(a):
    """export_model.py's arguments for SOURCE [NAME] OUT_DIR."""
    src, name, out = a.source, a.name, a.out_dir
    if os.path.isdir(src):
        kind = a.kind or ('object' if name else
                          'stage' if os.path.exists(os.path.join(src, 'header.bin')) else
                          'fighter' if os.path.exists(os.path.join(src, 'main.bin')) else None)
        if kind is None:
            raise SystemExit(f'{src}: no main.bin (fighter) or header.bin (stage); give an item / '
                             'projectile NAME, or --kind')
        if kind == 'object':
            if not name:
                raise SystemExit('an item / projectile needs its NAME (config.yaml NAME_hitbox)')
            return kind, [src, out, '--object', name]
        if name:
            raise SystemExit(f'NAME is for items / projectiles (or ROM exports), not a {kind} folder')
        return kind, [src, out] + (['--stage'] if kind == 'stage' else [])
    if not os.path.isfile(src):
        raise SystemExit(f'{src}: no such folder or ROM')
    if not name:
        raise SystemExit('a ROM export needs the NAME of the fighter, stage or item')
    kinds = [a.kind] if a.kind else rom_kind(name)
    if not kinds:
        raise SystemExit(f'{name!r}: not a fighter, stage or item name (export_model.py --help lists them)')
    if len(kinds) > 1:
        raise SystemExit(f'{name!r} is a {" and a ".join(kinds)}: pick one with --kind')
    flag = {'fighter': '--fighter', 'stage': '--stage', 'object': '--item'}[kinds[0]]
    return kinds[0], ['--rom', src, flag, name, out]


def cmd_export(a, extra):
    import export_model as EX
    kind, argv = export_argv(a)
    print(f'exporting {kind}: {" ".join(shlex.quote(x) for x in argv + extra)}')
    EX.main(argv + extra)
    if not a.no_blend:
        run_blender(blender_cmd(a.blender), 'build_blend.py', a.out_dir)
        print(f'-> {os.path.join(a.out_dir, "model.blend")}')


# ---------------------------------------------------------------------------- import

def cmd_import(a, extra):
    out_dir = a.out_dir
    man_path = os.path.join(out_dir, 'manifest.json')
    if not os.path.exists(man_path):
        raise SystemExit(f'{out_dir}: no manifest.json (not an export folder)')
    man = json.load(open(man_path))
    src = man.get('source') or {}
    obj = man.get('object') or {}
    kind = a.kind or src.get('kind') or ('stage' if obj.get('kind') == 'stage' else 'object' if obj else 'fighter')
    target = a.target or src.get('folder') or obj.get('char_dir')
    if not target:
        raise SystemExit('where to import to: give the TARGET folder (this export came from '
                         f'{src.get("rom") or "loose files"}, not a folder)')
    if not os.path.isdir(target):
        raise SystemExit(f'{target}: no such folder')

    work = os.path.abspath(a.work or os.path.join(out_dir, 'import_work'))
    if not a.no_blend:
        blend = a.blend or find_blend(out_dir)
        if os.path.exists(work):
            shutil.rmtree(work)                         # (no leftovers from an earlier import's layers / parts)
        run_blender(blender_cmd(a.blender), 'blend_to_gltf.py', work, blend)
    elif not os.path.exists(os.path.join(work, 'costumes.json')):
        raise SystemExit(f'--no-blend: {work} has no blend_to_gltf.py output')

    print(f'importing {kind} into {target}')
    if kind == 'fighter':
        import import_fighter
        import_fighter.main([target, work] + extra)
    elif kind == 'stage':
        import import_stage
        import_stage.main([target, work] + extra)
    else:
        name = a.name or src.get('name') or obj.get('name')
        if not name:
            raise SystemExit('which item / projectile: --name NAME')
        import import_object
        import_object.main([target, name, work] + extra)


# ---------------------------------------------------------------------------- main

def main(argv=None):
    sys.stdout.reconfigure(line_buffering=True)        # (in order with Blender's output)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0], epilog=__doc__.split('\n', 2)[2],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def blender_opts(p):
        p.add_argument('--blender', metavar='CMD', help='Blender command (e.g. "flatpak run org.blender.Blender")')
        p.add_argument('--kind', choices=KINDS, help='what it is, when it can\'t be told')

    p = sub.add_parser('export', help='game files -> OUT_DIR/model.blend')
    p.add_argument('source', metavar='SOURCE', help='fighter / stage folder, or a ROM')
    p.add_argument('rest', nargs='+', metavar='[NAME] OUT_DIR',
                   help='NAME: an item / projectile of the folder, or what to take from the ROM')
    p.add_argument('--no-blend', action='store_true', help="don't run Blender (no model.blend)")
    p.add_argument('--costumes', type=int, help='fighters: how many costumes (default: config.yaml num_costumes)')
    p.add_argument('--no-preview', action='store_true', help="don't render OUT_DIR/previews/")
    blender_opts(p)

    p = sub.add_parser('import', help='OUT_DIR/model.blend -> game files')
    p.add_argument('out_dir', metavar='OUT_DIR', help='the export folder')
    p.add_argument('target', nargs='?', metavar='TARGET',
                   help='folder to write to (default: the one it was exported from)')
    p.add_argument('--name', help='item / projectile name (default: the exported one)')
    p.add_argument('--blend', help='the .blend (default: OUT_DIR/model.blend)')
    p.add_argument('--work', help='blend_to_gltf.py output folder (default: OUT_DIR/import_work)')
    p.add_argument('--no-blend', action='store_true', help="don't run Blender: reuse the work folder")
    p.add_argument('--out', metavar='DIR', help='write the files to DIR instead (TARGET and its config.yaml '
                                                'are left as they are)')
    p.add_argument('--preview', metavar='DIR', help='fighters: render what was built into DIR')
    p.add_argument('--keep-render-modes', action='store_true',
                   help="fighters: keep cut-out materials' own render modes (not cloaking-safe)")
    p.add_argument('--no-gc', action='store_true', help="fighters: don't remove unreachable data")
    p.add_argument('--manifest', help="the export's manifest.json (default: the one the .blend was built from)")
    blender_opts(p)

    p = sub.add_parser('blend', help='(re)build OUT_DIR/model.blend from an export')
    p.add_argument('out_dir', metavar='OUT_DIR')
    p.add_argument('--blender', metavar='CMD')

    p = sub.add_parser('preview', help='(re)render OUT_DIR/previews/')
    p.add_argument('out_dir', metavar='OUT_DIR')
    p.add_argument('--size', type=int, default=200)

    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index('--') + 1:] if '--' in argv else []
    a = ap.parse_args(argv[:len(argv) - len(extra) - 1] if '--' in argv else argv)
    for opt in ('costumes', 'no_preview', 'out', 'preview', 'keep_render_modes', 'no_gc', 'manifest'):
        v = getattr(a, opt, None)                       # the common options of the scripts below
        if v not in (None, False):
            extra = ['--' + opt.replace('_', '-')] + ([] if v is True else [str(v)]) + extra
    if a.cmd == 'export':
        if len(a.rest) > 2:
            ap.error('export: SOURCE [NAME] OUT_DIR')
        a.name, a.out_dir = (a.rest if len(a.rest) == 2 else [None, a.rest[0]])
        cmd_export(a, extra)
    elif a.cmd == 'import':
        cmd_import(a, extra)
    elif a.cmd == 'blend':
        run_blender(blender_cmd(a.blender), 'build_blend.py', a.out_dir)
    else:
        import preview
        for path in preview.save_sheets(a.out_dir, None, a.size):
            print('wrote', path)


if __name__ == '__main__':
    main()
