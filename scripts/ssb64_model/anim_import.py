"""Fighter animations edited in Blender -> the character folder (import_fighter.py runs it).

blend_to_gltf.py samples the actions that changed since the export (keys, flags or loop), as
each joint's tracks per frame in the game's space. Each is written (figatree.encode_animation:
16-bit scripts, as few keys as play it within figatree.WRITE_TOL, Blender's key frames
preferred) to:
  - its own file, animations/<stem>.bin, when it came from one; main.asm's lines playing it
    get the new flags when they changed;
  - a new file when it was the base character's (`<action> (<PARENT>)`): animations/<action>.bin,
    and main.asm gets an edit_action_parameters (edit_menu_action_parameters) line per action
    that plays it, pointing at it, with its flags.
"""
import os
import re

import figatree as FT
import motions as MO


def _stem_of(name):
    first = name.split(' | ')[0]
    first = re.sub(r'\s*\([A-Z_]+\)$', '', first)
    return re.sub(r'[^A-Za-z0-9_]', '', first) or 'anim'


def _set_flags_arg(text, file_const, flags_hex):
    """main.asm: every Character.* call whose animation argument is file_const gets flags_hex as
    its flags argument. -> (text, lines changed)."""
    n = [0]

    def fix(m):
        kind, raw = m.group(1), m.group(2)
        args = MO._args(raw)
        anim_i, flags_i = (3, 5) if kind == 'add_new_action_params' else (2, 4)
        if len(args) <= flags_i or args[anim_i].upper() != file_const.upper():
            return m.group(0)
        if args[flags_i].strip() == flags_hex:
            return m.group(0)
        args[flags_i] = flags_hex
        n[0] += 1
        return f'Character.{kind}({", ".join(args)})'
    text = re.sub(r'Character\.(edit_action_parameters|add_new_action_params|edit_menu_action_parameters)'
                  r'\(([^\n]*)\)', fix, text)
    return text, n[0]


def _action_ref(label, scope):
    names, _ = MO.action_names()
    m = re.match(r'Menu (\d+)$', label)
    if m:
        return 'menu', m.group(1)
    if f'Action.{label}' in names:
        return 'action', f'Action.{label}'
    if scope and f'Action.{scope}.{label}' in names:
        return 'action', f'Action.{scope}.{label}'
    m = re.match(r'Action_([0-9A-Fa-f]+)$', label)
    if m:
        return 'action', f'0x{m.group(1).upper()}'
    return None, None


def _source_bytes(info, char_dir):
    """An exported animation's file bytes and internal head (manifest 'source': its own file, or a
    ROM file) -> (data, head) or (None, None)."""
    src = info.get('source') or ''
    if src.startswith('animations/'):
        p = os.path.join(char_dir, src)
        return (open(p, 'rb').read(), None) if os.path.exists(p) else (None, None)
    m = re.search(r'ROM file 0x([0-9A-Fa-f]+)', src)
    if not m:
        return None, None
    from SSB import SSBtbl, N64
    global _ROM
    if _ROM is None:
        _ROM = SSBtbl.fromROM(N64(open(MO.ORIGINAL_ROM, 'rb').read()))
    e = _ROM[int(m.group(1), 16)]
    return e.extract('data'), (e.tbl if e.tbl is not None else None)


_ROM = None


def apply(char_dir, out_dir, changed, manifest, cfg, f, jm=None):
    """Write the changed animations (blend_to_gltf.py fighter_animations) for fighter f (the
    character's model_files.load) into out_dir (the character folder, or --out). jm (jointmap):
    joints were removed, so EVERY animation is written: the edited ones encoded, the others
    their file without the removed joints' entries (scripts byte for byte); the base
    character's become the character's own files (main.asm lines for their actions)."""
    import jointmap as JM
    infos = (manifest or {}).get('fighter_animations') or {}
    jobs = []
    for c in changed or []:
        info = infos.get(c['export_name']) or {}
        flags = int(str(c['flags']), 16)
        written = FT.written_flags(flags)
        loops = {b: True for b in c['values']} if c.get('loop') else None
        # (joints added / removed: encoded straight in the new joint order, added joints' keys included)
        data = FT.encode_animation(f, written, c['values'], c['frames'], loops=loops, candidates=c.get('keys'),
                                   jm=jm if jm else None)
        jobs.append(dict(name=c['name'], info=info, data=data, written=written, flags=flags,
                         flags_before=c.get('flags_before'), why='edited in Blender'))
    if jm:
        done = {c['export_name'] for c in changed or []}
        n_missing = n_same = 0
        for name, info in infos.items():
            if name in done:
                continue
            data, head = _source_bytes(info, char_dir)
            if data is None:
                n_missing += 1
                print(f'  warning: animation {name}: its file ({info.get("source")}) wasn\'t found: '
                      'not renumbered (the game would play it on the wrong joints)')
                continue
            flags = int(str(info.get('flags') or '0'), 16)
            new = JM.remap_animation(f, data, flags, jm, head, name=name)
            if new is data:                # (no removed joint in its entries: the file stays as it is)
                n_same += 1
                continue
            jobs.append(dict(name=name, info=info, data=new,
                             written=flags, flags=flags, flags_before=info.get('flags'),
                             why=f'joints {jm.describe()}', quiet=True))
        for x, names_ in sorted(jm.notes.items()):
            print(f'  note: joint_{jm.idx(x):02d} (moved): in {len(names_)} animation(s) its new parent scales unevenly, '
                  'so its exact motion would need a shear the game can\'t store: the closest pose is written ('
                  + ', '.join(names_[:5]) + (' ...' if len(names_) > 5 else '') + ')')
        print(f'  animations: {len(jobs)} written: joints {jm.describe()}'
              + (f'; {n_same} untouched (their entries end before it)' if n_same else ''))
    if not jobs:
        return
    char = os.path.basename(os.path.normpath(char_dir))
    base = (cfg.get('definitions') or {}).get('base_character')
    cid = MO.CHAR_IDS.get(str(base).upper()) if base else None
    scope = MO.SCOPE_OF.get(cid)
    adir_in = os.path.join(char_dir, 'animations')
    adir = os.path.join(out_dir, 'animations')
    os.makedirs(adir, exist_ok=True)
    asm_in = os.path.join(char_dir, 'main.asm')
    # (newline='': its own line endings, CRLF or not, kept)
    asm_text = open(asm_in, encoding='utf-8', newline='').read() if os.path.exists(asm_in) else None
    asm_changed = False
    new_lines = []
    existing = {x.lower() for d in (adir_in, adir) if os.path.isdir(d) for x in os.listdir(d)}
    n_new = n_own = 0
    for c in jobs:
        info, data, written, flags = c['info'], c['data'], c['written'], c['flags']
        quiet = c.get('quiet')
        src = info.get('source') or ''
        if src.startswith('animations/'):                 # its own file
            stem = os.path.splitext(src[len('animations/'):])[0]
            old = os.path.join(adir_in, stem + '.bin')
            before = os.path.getsize(old) if os.path.exists(old) else 0
            open(os.path.join(adir, stem + '.bin'), 'wb').write(data)
            msg = f'  animation {stem}: {before} -> {len(data)} bytes'
            if asm_text is not None and written != int(str(c.get('flags_before') or '0'), 16):
                asm_text, n = _set_flags_arg(asm_text, f'File.{char.upper()}_ANIM_{stem.upper()}',
                                             f'0x{written:08X}')
                asm_changed |= bool(n)
                msg += f'; flags {written:08X} on {n} main.asm line(s)'
            elif written != flags:
                msg += f' (written in the 16-bit format: flags {written:08X} instead of {flags:08X})'
            n_own += 1
            if not quiet:
                print(msg)
            continue
        # the parent's (or a ROM fighter's): a new file, and main.asm lines for its actions
        stem = _stem_of(c['name'])
        k, s0 = 2, stem
        while stem.lower() + '.bin' in existing:
            stem, k = f'{s0}{k}', k + 1
        existing.add(stem.lower() + '.bin')
        open(os.path.join(adir, stem + '.bin'), 'wb').write(data)
        const = f'File.{char.upper()}_ANIM_{stem.upper()}'
        lines = []
        fl = '-1' if quiet else f'0x{written:08X}'       # (unedited: each action keeps its own flags)
        for label in info.get('actions') or []:
            kind, ref = _action_ref(label, scope)
            if kind == 'menu':
                lines.append(f'    Character.edit_menu_action_parameters({char.upper()}, {ref}, {const}, -1, '
                             f'{fl})')
            elif kind == 'action':
                lines.append(f'    Character.edit_action_parameters({char.upper()}, {ref}, {const}, -1, '
                             f'{fl})')
            else:
                print(f'  note: {stem}: action {label!r} has no name main.asm can use; add its line by hand')
        new_lines += [f'    // {c["name"]} ({c["why"]}: ssb64_model import)'] + lines
        n_new += 1
        if not quiet:
            print(f'  animation {stem}: new file ({len(data)} bytes) for {len(lines)} action(s) of {base}')
    if jm:
        print(f'  animations: {n_own} own files rewritten, {n_new} new files ({base}\'s)')
    if new_lines and asm_text is not None:
        calls = list(re.finditer(r'[^\n]*Character\.(edit_action_parameters|edit_menu_action_parameters)\([^\n]*\n',
                                 asm_text))
        if calls:
            at = calls[-1].end()
        else:                          # (none yet: inside the character's scope, before its closing brace)
            close = asm_text.rstrip().rfind('}')
            at = asm_text.rfind('\n', 0, close) + 1 if close >= 0 else len(asm_text)
        nl = '\r\n' if '\r\n' in asm_text else '\n'
        asm_text = asm_text[:at] + nl.join(new_lines) + nl + asm_text[at:]
        asm_changed = True
    if asm_changed and asm_text is not None:
        open(os.path.join(out_dir, 'main.asm'), 'w', encoding='utf-8', newline='').write(asm_text)
        print(f'wrote {out_dir}/main.asm')
