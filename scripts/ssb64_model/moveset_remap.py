"""Joints added / removed / moved (jointmap.py): which moveset events now name the wrong joint.

Motion events name joints by number (hitboxes, effects, hurtbox changes, model parts). The
character's actions run the base character's scripts (`-1` as the command in main.asm), so after a
renumbering some of those events land on other joints. Nothing is rewritten: the import lists
the actions whose scripts name joints whose number changed (or that are gone), with the
renumbering, so their scripts can be given their own versions in main.asm where it matters; and
the same for the character's own moveset/*.bin. ASM code naming joints isn't read either.
"""
import os

import jointmap as JM
import motions as MO


class _Count:
    """A JointMap stand-in for patch_script_events that only counts (and tolerates removed joints)."""

    def __init__(self, jm):
        self.jm, self.changed, self.removed = jm, 0, 0

    def game(self, g):
        n = self.jm.game(g)
        if n is None:
            self.removed += 1
            return g
        if n != g:
            self.changed += 1
        return g                        # (left as it is: only counted)


def _count(data, start, jm, ptrs=None, end=None):
    c = _Count(jm)
    try:
        JM.patch_script_events(bytearray(data), start, c, ptrs, end=end)
    except ValueError:
        return None
    return c


def apply(char_dir, out_dir, cfg, jm):
    from SSB import SSBtbl, N64
    from smashremix_extra.constants import PRIMARY_MOVESETS
    import model_files as MF
    base = str((cfg.get('definitions') or {}).get('base_character') or '').upper()
    cid = MO.CHAR_IDS.get(base)
    renum = ', '.join(f'{o + 4} -> {n + 4}' for o, n in jm.renumbered())
    if cid is None or base not in PRIMARY_MOVESETS:
        print(f'  moveset: base character {base or "?"} unknown: its events not checked')
        return
    fid = int(PRIMARY_MOVESETS[base], 16)
    e = SSBtbl.fromROM(N64(open(MO.ORIGINAL_ROM, 'rb').read()))[fid]
    data = e.extract('data')
    ptrs = MF.chain(bytes(data), e.tbl) if e.tbl is not None else {}
    van = MO.Vanilla()
    params = van.params(cid)
    hits = []
    for i, m in MO.rom_motions(cid).items():
        if not isinstance(i, int) or i >= len(params) or not m['actions']:
            continue
        start = params[i][1]
        if not 0 <= start < len(data):
            continue
        c = _count(data, start, jm, ptrs)
        if c is not None and (c.changed or c.removed):
            hits.append((m['actions'], c.changed, c.removed))
    if hits:
        print(f'  moveset: {len(hits)} of {base}\'s action scripts (played with -1) name joints whose number changed '
              f'(game ids {renum}' + (f'; removed: {", ".join(str(i + 4) for i in sorted(jm.removed))}' if jm.removed else '')
              + '): their hitboxes / effects / hurtbox or model part changes now land on other joints:')
        for acts, ch, rm in hits:
            print(f'    {" / ".join(acts[:3])}{" ..." if len(acts) > 3 else ""}: {ch} event(s)'
                  + (f', {rm} on a removed joint' if rm else ''))
    else:
        print(f'  moveset: none of {base}\'s action scripts name a joint whose number changed')
    mdir = os.path.join(char_dir, 'moveset')
    if os.path.isdir(mdir):
        for fn in sorted(os.listdir(mdir)):
            if fn.lower().endswith('.bin'):
                raw = open(os.path.join(mdir, fn), 'rb').read()
                c = _count(raw, 0, jm, None, end=len(raw))
                if c is None:
                    print(f'    moveset/{fn}: not read (events it doesn\'t know): check its joint ids by hand')
                elif c.changed or c.removed:
                    print(f'    moveset/{fn}: {c.changed} event(s) name joints whose number changed'
                          + (f', {c.removed} a removed joint' if c.removed else ''))
    asm = [fn for fn in os.listdir(char_dir) if fn.lower().endswith('.asm')]
    if asm:
        print(f'  note: ASM naming joints by number ({", ".join(sorted(asm))}) isn\'t checked: renumbered {renum} (game ids)')
